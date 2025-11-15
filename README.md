# Truth Social Monitor

A Python application that monitors Truth Social posts from specified users and forwards them to Discord.

## Features

- Monitors Truth Social users' posts using Mastodon-compatible API
- Forwards posts to Discord via webhooks
- Stores processed posts in Supabase to avoid duplicates
- Supports media attachments (images, videos, GIFs)
- Rate limiting for Discord notifications
- Automatic retries for failed requests
- Comprehensive error handling and logging

## Prerequisites

- Python 3.8 or higher
- Supabase account and project (create one at [supabase.com](https://supabase.com))
- Discord webhook URL
- Flaresolverr to run requests through

## Supabase Setup

1. Create a Supabase project at [supabase.com](https://supabase.com)
2. Get your project URL and anon key from the project settings
3. Create a table named `posts` (or use the name specified in `SUPABASE_TABLE`) with the following schema:

```sql
CREATE TABLE posts (
  id TEXT PRIMARY KEY,
  content TEXT,
  created_at TEXT,
  sent_at TIMESTAMPTZ,
  username TEXT,
  display_name TEXT,
  media_attachments JSONB
);
```

Alternatively, you can use the Supabase dashboard to create the table with these columns.

## Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/darrenwatt/truthy.git
   cd truthy
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Create a `.env` file with your configuration:
   ```env
   # Logging
   LOG_LEVEL=INFO
   APPNAME="Truth Social Monitor"
   ENV=PROD
   REPEAT_DELAY=300

   # Discord
   DISCORD_NOTIFY=true
   DISCORD_USERNAME="Truth Social Bot"
   DISCORD_WEBHOOK_URL=your_webhook_url_here

   # Supabase
   SUPABASE_URL=https://your-project.supabase.co
   SUPABASE_KEY=your-anon-key-here
   SUPABASE_TABLE=posts

   # Truth Social
   TRUTH_USERNAME=username_to_monitor
   TRUTH_INSTANCE=truthsocial.com

   # Request Settings
   REQUEST_TIMEOUT=30
   MAX_RETRIES=3

   # Flaresolverr
   FLARESOLVERR_ADDRESS=localhost
   FLARESOLVERR_PORT=8191
   ```

## Usage

Run flaresolverr locally with docker compose (supplied):
```bash
docker compose up -d
```

Run the monitor:
```bash
python main.py
```

Or using Docker:
```bash
docker build -t truth-social-monitor .
docker run -d --env-file .env truth-social-monitor
```

## Configuration

All configuration is handled via environment variables, typically set in a `.env` file at the project root.

### Required Environment Variables

| Variable               | Description                                                      | Example/Default                |
|------------------------|------------------------------------------------------------------|--------------------------------|
| `TRUTH_USERNAME`       | The Truth Social username to monitor                             | `realDonaldTrump`              |
| `SUPABASE_URL`         | Supabase project URL                                            | `https://xxx.supabase.co`      |
| `SUPABASE_KEY`         | Supabase anon/service key                                        | *(required)*                   |

### Optional Environment Variables

| Variable               | Description                                                      | Example/Default                |
|------------------------|------------------------------------------------------------------|--------------------------------|
| `LOG_FORMAT`           | Python logging format string                                     | See `config.py` for default    |
| `LOG_LEVEL`            | Logging level                                                    | `INFO`                         |
| `APPNAME`              | Application name                                                 | `Truth Social Monitor`         |
| `ENV`                  | Environment name                                                 | `DEV`                          |
| `REPEAT_DELAY`         | Delay between checks (seconds)                                   | `300`                          |
| `DISCORD_NOTIFY`       | Enable Discord notifications (`true`/`false`)                    | `true`                         |
| `DISCORD_USERNAME`     | Username for Discord bot                                         | `Truth Social Bot`             |
| `DISCORD_WEBHOOK_URL`  | Discord webhook URL                                              | *(required if notify enabled)* |
| `SUPABASE_TABLE`       | Supabase table name                                              | `posts`                        |
| `TRUTH_INSTANCE`       | Truth Social instance domain                                     | `truthsocial.com`              |
| `POST_TYPE`            | Type of posts to monitor                                         | `post`                         |
| `REQUEST_TIMEOUT`      | HTTP request timeout (seconds)                                   | `30`                           |
| `MAX_RETRIES`          | Max HTTP request retries                                         | `3`                            |
| `FLARESOLVERR_ADDRESS` | Flaresolverr server address                                     | `localhost`                   |
| `FLARESOLVERR_PORT`    | Flaresolverr server port                                        | `8191`                        |

### Example `.env` file

```env
LOG_LEVEL=INFO
APPNAME="Truth Social Monitor"
ENV=DEV
REPEAT_DELAY=300

DISCORD_NOTIFY=true
DISCORD_USERNAME="Truth Social Bot"
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...

SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your-anon-key-here
SUPABASE_TABLE=posts

TRUTH_USERNAME=realDonaldTrump
TRUTH_INSTANCE=truthsocial.com
POST_TYPE=post

REQUEST_TIMEOUT=30
MAX_RETRIES=3

FLARESOLVERR_ADDRESS=localhost
FLARESOLVERR_PORT=8191
```

### Validation

- If `DISCORD_NOTIFY` is `true`, `DISCORD_WEBHOOK_URL` **must** be set.
- `TRUTH_USERNAME`, `SUPABASE_URL`, and `SUPABASE_KEY` are always required.

---
For more details, see the `config.py` file.

## Error Handling

The application includes comprehensive error handling:
- Automatic retries for network failures
- Rate limiting for Discord notifications
- Validation of configuration settings
- Detailed logging of errors and operations
- Safe storage of processed posts

## Contributing

Feel free to submit issues and pull requests.

## License

MIT License
