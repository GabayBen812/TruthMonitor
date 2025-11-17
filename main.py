import logging
import time
from datetime import datetime, timezone
import requests
from config import Config
from discord_webhook import DiscordWebhook
from supabase import create_client, Client
from urllib.parse import urlencode
from functools import wraps
from ratelimit import limits, sleep_and_retry
import backoff
from bs4 import BeautifulSoup
import re

# Configure logging
config = Config()
logging.basicConfig(
    format=config.LOG_FORMAT,
    level=logging.DEBUG if config.LOG_LEVEL.upper() == 'DEBUG' else logging.INFO,
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# In-memory cache to track processed posts (fallback if Supabase fails)
processed_posts_cache = set()

# Cache user ID to avoid repeated lookups
cached_user_id = None

# Rate limit: 1 request per 2 seconds for Discord
DISCORD_CALLS = 30
DISCORD_PERIOD = 60

@sleep_and_retry
@limits(calls=DISCORD_CALLS, period=DISCORD_PERIOD)
def rate_limited_discord_send(webhook):
    """Execute Discord webhook with rate limiting"""
    return webhook.execute()

@backoff.on_exception(
    backoff.expo,
    (requests.exceptions.RequestException, requests.exceptions.HTTPError),
    max_tries=config.MAX_RETRIES
)
def make_request(url, headers):
    """Make HTTP request with retry mechanism"""
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        return response
    except requests.exceptions.HTTPError as e:
        logger.error(f"HTTP Error {e.response.status_code} for URL {url}")
        logger.error(f"Response headers: {e.response.headers}")
        logger.error(f"Response body: {e.response.text[:500]}")  # First 500 chars of error response
        raise



def make_flaresolverr_request(url, headers=None, params=None):
    """Use FlareSolverr to fetch a URL and return a response-like object."""
    flaresolverr_url = f"http://{config.FLARESOLVERR_ADDRESS}:{config.FLARESOLVERR_PORT}/v1"
    payload = {
        "cmd": "request.get",
        "url": url,
        "maxTimeout": 15000,  # Reduced from 25000 to 15000 for faster timeouts
    }
    if headers:
        payload["headers"] = headers
    if params:
        from urllib.parse import urlencode
        url = url + "?" + urlencode(params)
        payload["url"] = url

    logger.info(f"Making FlareSolverr request: {url} (params={params})")

    try:
        resp = requests.post(flaresolverr_url, json=payload)
        resp.raise_for_status()
        result = resp.json()
        if result.get("status") != "ok":
            logger.error(f"FlareSolverr error: {result}")
            raise Exception(f"FlareSolverr error: {result}")
        response_content = result["solution"]["response"]
        logger.debug(f"FlareSolverr raw response (first 500 chars): {response_content[:500]}")
        # Mimic a requests.Response object for .json() and .text
        class FakeResponse:
            def __init__(self, content):
                self._content = content
            def json(self):
                import json
                from bs4 import BeautifulSoup
                # Try to parse as JSON directly
                try:
                    return json.loads(self._content)
                except Exception:
                    # Try to extract JSON from <pre>...</pre> in HTML
                    soup = BeautifulSoup(self._content, "html.parser")
                    pre = soup.find("pre")
                    if pre:
                        try:
                            return json.loads(pre.text)
                        except Exception as e:
                            logger.error(f"Failed to parse JSON from <pre>: {e}")
                            logger.error(f"<pre> content (first 500 chars): {pre.text[:500]}")
                            raise
                    logger.error("No <pre> tag found in FlareSolverr HTML response")
                    logger.error(f"HTML content (first 500 chars): {self._content[:500]}")
                    raise
            @property
            def text(self):
                return self._content
        return FakeResponse(response_content)
    except Exception as e:
        logger.error(f"FlareSolverr request failed for {url}: {e}")
        raise



def connect_supabase():
    """Connect to Supabase and return the client"""
    try:
        supabase: Client = create_client(config.SUPABASE_URL, config.SUPABASE_KEY)
        logger.info("Successfully connected to Supabase")
        
        # Test the connection by trying to query the table
        try:
            test_response = supabase.table(config.SUPABASE_TABLE).select("id").limit(1).execute()
            logger.info(f"Successfully tested Supabase connection to table '{config.SUPABASE_TABLE}'")
        except Exception as e:
            error_msg = str(e).lower()
            if "permission denied" in error_msg or "row-level security" in error_msg:
                logger.error("SUPABASE RLS WARNING: Row Level Security may be blocking queries.")
                logger.error("Please check your Supabase RLS policies for SELECT operations.")
            elif "relation" in error_msg and "does not exist" in error_msg:
                logger.error(f"SUPABASE TABLE ERROR: Table '{config.SUPABASE_TABLE}' does not exist.")
                logger.error("Please create the table in your Supabase project.")
            else:
                logger.warning(f"Could not test Supabase table access: {e}")
                logger.warning("The bot will continue, but posts may not be saved properly.")
        
        return supabase
    except Exception as e:
        logger.error(f"Failed to connect to Supabase: {e}")
        raise

def is_post_processed(supabase, post_id):
    """Check if a post has already been processed (checks both Supabase and cache)"""
    # First check in-memory cache (fastest)
    if post_id in processed_posts_cache:
        logger.debug(f"Post {post_id} found in cache")
        return True
    
    # Then check Supabase
    try:
        response = supabase.table(config.SUPABASE_TABLE).select("id").eq("id", post_id).execute()
        exists = len(response.data) > 0
        if exists:
            logger.debug(f"Post {post_id} found in database")
            # Add to cache for faster future lookups
            processed_posts_cache.add(post_id)
        return exists
    except Exception as e:
        logger.error(f"Error checking if post is processed: {e}")
        logger.error(f"Post ID: {post_id}")
        # If we can't check Supabase, rely on cache only
        return post_id in processed_posts_cache

def mark_post_processed(supabase, post):
    """Mark a post as processed in Supabase with additional metadata"""
    try:
        # Build document with only the essential fields that should exist in the table
        doc = {
            "id": str(post["id"]),  # Ensure it's a string
            "content": post.get("content", "") or "",
            "created_at": post.get("created_at", ""),
            "sent_at": datetime.now(timezone.utc).isoformat(),
            "username": post.get("account", {}).get("username", "") or ""
        }
        
        # Only add optional fields if they exist in the post
        display_name = post.get("account", {}).get("display_name")
        if display_name:
            doc["display_name"] = display_name
        
        media_attachments = [
            {
                "type": m.get("type"),
                "url": m.get("url") or m.get("preview_url")
            }
            for m in post.get("media_attachments", [])
            if m.get("type") in ["image", "video", "gifv"]
        ]
        if media_attachments:
            doc["media_attachments"] = media_attachments
        # Use upsert to handle duplicate keys gracefully
        # Upsert will update if exists, insert if not (based on primary key)
        logger.debug(f"Attempting to upsert post {post['id']} to table {config.SUPABASE_TABLE}")
        logger.debug(f"Document to save: {doc}")
        response = supabase.table(config.SUPABASE_TABLE).upsert(doc).execute()
        
        logger.debug(f"Supabase response: {response}")
        logger.debug(f"Response data: {response.data if hasattr(response, 'data') else 'No data attribute'}")
        
        # Verify the post was actually saved
        if response.data and len(response.data) > 0:
            logger.info(f"Successfully marked post {post['id']} as processed. Response: {response.data}")
        else:
            logger.warning(f"Upsert completed but no data returned for post {post['id']}")
            logger.warning(f"Response object: {response}")
            # Verify by checking if it exists
            verify_response = supabase.table(config.SUPABASE_TABLE).select("id").eq("id", post["id"]).execute()
            logger.debug(f"Verification query result: {verify_response.data}")
            if len(verify_response.data) == 0:
                raise Exception(f"Post {post['id']} was not saved to database after upsert. Response was: {response}")
            else:
                logger.info(f"Post {post['id']} verified to exist in database despite empty upsert response")
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Error marking post as processed: {e}")
        logger.error(f"Post ID: {post.get('id', 'unknown')}")
        
        # Check for common Supabase errors
        if "permission denied" in error_msg.lower() or "row-level security" in error_msg.lower():
            logger.error("SUPABASE RLS ERROR: Row Level Security (RLS) is blocking the insert.")
            logger.error("Please check your Supabase RLS policies for the table.")
            logger.error("You may need to create a policy that allows INSERT operations.")
        elif "duplicate key" in error_msg.lower():
            logger.warning(f"Post {post.get('id')} already exists, but that's okay (upsert should handle this)")
            # If it's just a duplicate, we can consider it processed
            return
        elif "relation" in error_msg.lower() and "does not exist" in error_msg.lower():
            logger.error("SUPABASE TABLE ERROR: The table does not exist.")
            logger.error(f"Please verify the table name '{config.SUPABASE_TABLE}' exists in your Supabase project.")
        
        # Re-raise to prevent the post from being considered processed
        raise

def send_to_discord(message, media_attachments=None):
    """Send a message to Discord with rate limiting and retries"""
    if not message:
        logger.warning("Empty message, skipping Discord notification")
        return
        
    try:
        webhook = DiscordWebhook(
            url=config.DISCORD_WEBHOOK_URL,
            username=config.DISCORD_USERNAME,
            content=message,
            rate_limit_retry=True,
            delay_between_retries=10  # Wait 10 seconds between retries
        )
        
        # Handle media attachments
        if media_attachments:
            for media in media_attachments:
                if media.get('type') in ['image', 'video', 'gifv']:
                    url = media.get('url') or media.get('preview_url')
                    if url:
                        content, filename = download_media(url)
                        if content and filename:
                            webhook.add_file(file=content, filename=filename)
        
        logger.info("Sending Discord webhook...")
        response = rate_limited_discord_send(webhook)
        status_code = response.status_code
        
        if status_code == 400:
            logger.error(f"Discord 400 error. Message length: {len(message)}")
            logger.error(f"Message content (first 500 chars): {message[:500]}")
            logger.error(f"Response body: {response.text}")
        elif status_code == 429:  # Too Many Requests
            retry_after = response.json().get('retry_after', 5)
            logger.warning(f"Discord rate limit hit, waiting {retry_after} seconds")
            time.sleep(retry_after)
            response = webhook.execute()
            status_code = response.status_code
            
        if status_code not in range(200, 300):
            raise Exception(f"Discord returned status code {status_code}: {response.text}")
            
        logger.info("Successfully sent message to Discord")
    except Exception as e:
        logger.error(f"Error sending message to Discord: {e}")
        raise

def is_retweet(content):
    """Check if a post is a retweet by looking for RT pattern at the beginning"""
    if not content:
        return False
    
    # Strip HTML tags first for accurate detection
    text = BeautifulSoup(content, 'html.parser').get_text().strip()
    
    # Check if content starts with RT followed by space or @ (common retweet patterns)
    # Examples: "RT @username", "RT username", etc.
    text_upper = text.upper()
    return text_upper.startswith('RT ') or text_upper.startswith('RT@')

def clean_html_and_format(text):
    """Clean HTML tags and format text for Discord"""
    if not text:
        return ""
    
    # Parse HTML with BeautifulSoup
    soup = BeautifulSoup(text, 'html.parser')
    
    # Convert <br> and </p> to newlines
    for br in soup.find_all(['br', 'p']):
        br.replace_with('\n' + br.text)
    
    # Get text and clean up extra whitespace
    text = soup.get_text()
    
    # Replace multiple newlines with double newline
    text = re.sub(r'\n\s*\n', '\n\n', text)
    
    # Clean up extra whitespace
    text = re.sub(r' +', ' ', text)
    text = text.strip()
    
    # Convert URLs to clickable format if not already
    url_pattern = r'(?<![\(\[])(https?://\S+)(?![\)\]])'
    text = re.sub(url_pattern, r'<\1>', text)
    
    return text

def format_discord_message(post):
    """Format a post for Discord with media attachments and truncation"""
    if not isinstance(post, dict):
        logger.error(f"Invalid post format: {post}")
        return None

    try:
        created_at = datetime.fromisoformat(post.get('created_at', '').replace('Z', '+00:00'))
        content = post.get('content') or post.get('text', '')
        account = post.get('account', {})
        username = account.get('username') or config.TRUTH_USERNAME
        display_name = account.get('display_name', username)
        
        # Clean and format the content
        content = clean_html_and_format(content)
        
        # Format message parts with exact newlines
        post_type = config.POST_TYPE.capitalize()  # Ensure first letter is capitalized
        header = f"**New {post_type} from {display_name} (@{username})**\n"
        footer = f"\n*Posted at: {created_at.strftime('%B %d, %Y at %I:%M %p %Z')}*"
        
        # Calculate max content length with safety margin
        max_content_length = 1950 - len(header) - len(footer)
        
        # Truncate content if necessary
        if len(content) > max_content_length:
            truncated_length = max_content_length - 3
            content = content[:truncated_length] + "..."
        
        # Build final message without media URLs (they'll be embedded)
        final_message = header + content + footer
        
        # Final safety check
        if len(final_message) > 2000:
            logger.warning(f"Message too long ({len(final_message)} chars), applying emergency truncation")
            return final_message[:1997] + "..."
        
        return final_message
        
    except Exception as e:
        logger.error(f"Error formatting post: {e}")
        return None

def download_media(url):
    """Download media from URL and return the content and filename"""
    try:
        response = requests.get(url, stream=True)
        response.raise_for_status()
        # Get filename from URL or Content-Disposition header
        filename = url.split('/')[-1].split('?')[0]  # Remove query parameters
        content_type = response.headers.get('content-type', '').lower()
        
        # Ensure proper file extension based on content type
        if 'image/jpeg' in content_type and not filename.lower().endswith(('.jpg', '.jpeg')):
            filename += '.jpg'
        elif 'image/png' in content_type and not filename.lower().endswith('.png'):
            filename += '.png'
        elif 'image/gif' in content_type and not filename.lower().endswith('.gif'):
            filename += '.gif'
        elif 'video/' in content_type and not filename.lower().endswith(('.mp4', '.mov', '.webm')):
            filename += '.mp4'
            
        return response.content, filename
    except Exception as e:
        logger.error(f"Error downloading media from {url}: {e}")
        return None, None

def get_truth_social_posts():
    """Get posts from Truth Social using Mastodon API via FlareSolverr"""
    global cached_user_id
    
    try:
        # Prepare headers that look like a real browser
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'application/json',
            'Accept-Language': 'en-US,en;q=0.5',
            'Referer': f'https://{config.TRUTH_INSTANCE}/@{config.TRUTH_USERNAME}',
            'Origin': f'https://{config.TRUTH_INSTANCE}'
        }

        # Use cached user ID to avoid repeated lookups (optimization)
        if cached_user_id is None:
            lookup_url = f'https://{config.TRUTH_INSTANCE}/api/v1/accounts/lookup?acct={config.TRUTH_USERNAME}'
            response = make_flaresolverr_request(lookup_url, headers)
            user_data = response.json()
            
            if not user_data or 'id' not in user_data:
                raise ValueError(f"Could not find user ID for {config.TRUTH_USERNAME}")
                
            cached_user_id = user_data['id']
            logger.debug(f"Found and cached user ID: {cached_user_id}")
        
        user_id = cached_user_id
        
        # Optimized: Only fetch 5 posts since we only need the latest new one
        posts_url = f'https://{config.TRUTH_INSTANCE}/api/v1/accounts/{user_id}/statuses'
        params = {
            'exclude_replies': 'true',
            'exclude_reblogs': 'true',
            'limit': '5'  # Reduced from 40 to 5 for faster processing
        }
        
        response = make_flaresolverr_request(posts_url, params=params, headers=headers)
        posts = response.json()
        
        if not isinstance(posts, list):
            raise ValueError(f"Invalid posts response: {posts}")
            
        logger.info(f"Retrieved {len(posts)} posts")
        return posts
        
    except Exception as e:
        logger.error(f"Error getting Truth Social posts: {e}")
        # Reset cached user ID on error in case it's invalid
        cached_user_id = None
        return []

def main():
    logger.info("Starting Truth Social monitor...")
    
    # Connect to Supabase
    try:
        supabase_client = connect_supabase()
    except Exception as e:
        logger.error(f"Failed to connect to Supabase in main: {e}")
        raise

    while True:
        try:
            # Get posts
            posts = get_truth_social_posts()
            
            # Process posts in reverse chronological order (newest first)
            # Only process the LATEST new post to avoid spamming on startup
            for post in sorted(posts, key=lambda x: x.get('created_at', ''), reverse=True):
                # Validate post structure
                if not isinstance(post, dict) or 'id' not in post:
                    logger.warning(f"Invalid post structure: {post}")
                    continue
                    
                # Skip if already processed
                if is_post_processed(supabase_client, post['id']):
                    logger.debug(f"Post {post['id']} already processed, skipping")
                    continue
                
                # Skip retweets - filter them out
                content = post.get('content') or post.get('text', '')
                if is_retweet(content):
                    logger.info(f"Post {post['id']} is a retweet, skipping")
                    # Mark as processed so we don't check it again
                    processed_posts_cache.add(post['id'])
                    try:
                        # Save to Supabase to mark as processed (even though we're not sending it)
                        mark_post_processed(supabase_client, post)
                    except Exception as e:
                        logger.debug(f"Could not save retweet to Supabase (non-critical): {e}")
                    continue
                
                # Found a new post - process only this one (the latest)
                logger.info(f"Processing new post {post['id']} (latest unprocessed post)")
                
                # Format message first
                message = format_discord_message(post)
                if not message:
                    logger.warning(f"Could not format message for post {post['id']}, skipping")
                    continue
                
                media_attachments = post.get('media_attachments', [])
                post_id = post['id']
                
                try:
                    # Try to save to Supabase first
                    mark_post_processed(supabase_client, post)
                    logger.info(f"Successfully saved post {post_id} to Supabase")
                except Exception as e:
                    logger.error(f"Failed to save post {post_id} to Supabase: {e}")
                    logger.warning(f"Will still send to Discord and use cache to prevent duplicates")
                
                # Add to cache to prevent duplicates (even if Supabase save failed)
                processed_posts_cache.add(post_id)
                
                # Send to Discord (even if Supabase save failed, we use cache to prevent duplicates)
                try:
                    send_to_discord(message, media_attachments)
                    logger.info(f"Successfully sent post {post_id} to Discord")
                except Exception as e:
                    logger.error(f"Failed to send post {post_id} to Discord: {e}")
                    # Remove from cache if Discord send failed, so we can retry later
                    processed_posts_cache.discard(post_id)
                    raise
                
                # IMPORTANT: Only process the latest new post, then break
                # This prevents spamming old posts on startup
                logger.info(f"Processed latest new post. Stopping here to avoid processing older posts.")
                break
                
        except Exception as e:
            logger.error(f"Error in main loop: {e}")
        
        delay = int(config.REPEAT_DELAY)
        if delay < 5:
            logger.warning(f"REPEAT_DELAY is very low ({delay}s). Consider at least 5 seconds to avoid rate limiting.")
        logger.info(f"Waiting {delay} seconds before next check...")
        time.sleep(delay)

if __name__ == "__main__":
    main()
