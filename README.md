# Idealista Notifier Bot

## Overview

This project automatically scrapes Idealista for new apartment listings in Madrid and sends real-time notifications via **Pushover**. It filters listings based on predefined criteria and sends notifications including:

- 📍 Location
- 💰 Price
- 🛏️ Rooms
- 📏 Size (m²)
- 🏢 Floor
- 🔗 Direct link to the listing

**Special features:**
- 🚨 High-priority notifications for "Ático" listings
- ⚠️ Error alerts when Idealista blocks access (Error 403)
- 🔄 Automatic tracking to avoid duplicate notifications

## Project Structure

```text
idealista-notifier/
│── src/
│   ├── scraper.py          # Scrapes Idealista & sends Pushover notifications
│── data/                   # Stores seen listings and error logs (auto-created)
│── .env                    # Stores API keys (excluded from Git)
│── requirements.txt        # Python dependencies
│── Dockerfile              # Container configuration
│── docker-compose.yml      # Deployment configuration
│── README.md               # Project documentation
```

## How It Works

1. The script scrapes Idealista every 1-2 minutes (randomized)
2. Uses **cloudscraper** to bypass Cloudflare's anti-bot protection
3. It filters out unwanted areas, keywords, and floor types (configurable)
4. When a new listing appears, it extracts all relevant details
5. Sends a formatted HTML notification to your Pushover app
6. Tracks seen listings to avoid duplicates (stores last 100 listings)

## Setup & Installation

### 1. Clone the Repository

```bash
git clone https://github.com/yourusername/idealista-notifier.git
cd idealista-notifier
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Get Pushover Credentials

You need two keys from Pushover:

#### A. **User Key** (identifies you)
1. Go to [pushover.net](https://pushover.net) and create an account
2. Your **User Key** is displayed on the main dashboard
3. Download the Pushover app on your phone ([iOS](https://apps.apple.com/app/pushover-notifications/id506088175) / [Android](https://play.google.com/store/apps/details?id=net.superblock.pushover))

#### B. **API Token** (identifies this application)
1. Go to [pushover.net/apps/build](https://pushover.net/apps/build)
2. Create a new application:
   - **Name:** Idealista Notifier
   - **Type:** Application
   - **Description:** Monitors Idealista for new listings
3. Copy the **API Token/Key** shown after creation

### 4. Set Up Environment Variables

Create a `.env` file in the root directory:

```bash
PUSHOVER_USER_KEY=your_user_key_here
PUSHOVER_API_TOKEN=your_api_token_here
```

Replace with your actual keys from step 3.

### 5. Run the Bot

```bash
python3 src/scraper.py
```

You should start receiving notifications on your phone via the Pushover app!

## Configuration

You can customize the scraper by editing `src/scraper.py`:

```python
# Maximum price filter
MAX_PRICE = 200000

# Neighborhoods to exclude
EXCLUDED_AREAS = ["Raval", "Gòtic", "Barceloneta"]

# Keywords to filter out
EXCLUDED_TERMS = ["Alquiler temporal", "estudio"]

# Floors to exclude
EXCLUDED_FLOORS = ["Entreplanta", "Bajo"]

# Maximum listings to track
MAX_LISTINGS = 100
```

## Docker Setup

### Run Locally with Docker

To run the bot using Docker:

```bash
docker build -t idealista-bot .
docker run -d --restart unless-stopped \
  -e PUSHOVER_USER_KEY=your_user_key \
  -e PUSHOVER_API_TOKEN=your_api_token \
  --name idealista-bot \
  idealista-bot
```

### Run with Docker Compose

1. Create a `.env` file with your credentials (see step 4 above)
2. Run:

```bash
docker-compose up -d
```

This ensures the bot:
- ✅ Persists data between restarts
- ✅ Auto-restarts if it crashes
- ✅ Runs in the background

### View Logs

```bash
docker-compose logs -f
```

## Deploy to Railway.app

### 1. Install Railway CLI

```bash
curl -fsSL https://railway.app/install.sh | sh
railway login
```

### 2. Link Project & Deploy

```bash
railway init
railway up
```

### 3. Set Environment Variables

```bash
railway variables set PUSHOVER_USER_KEY=your_user_key_here
railway variables set PUSHOVER_API_TOKEN=your_api_token_here
```

### 4. Check Logs & Status

```bash
railway logs -f
railway status
```

The bot will now run 24/7 in the cloud! 🎉

## Notification Priority Levels

The bot uses Pushover's priority system:

- **Priority 0** (Normal): Regular apartment listings
- **Priority 1** (High): Ático listings (bypasses quiet hours)
- **Priority 1** (High): Error 403 alerts

You can customize priority levels in the `send_pushover_notification()` function.

## Troubleshooting

### No notifications received?

1. ✅ Check your `.env` file has correct credentials
2. ✅ Verify Pushover app is installed on your phone
3. ✅ Test with: `curl -s -F "token=YOUR_API_TOKEN" -F "user=YOUR_USER_KEY" -F "message=Test" https://api.pushover.net/1/messages.json`
4. ✅ Check logs: `docker-compose logs -f` or `python3 src/scraper.py`

### Error 403 from Idealista?

Idealista may temporarily block your IP. The bot will:
- Send you a high-priority alert
- Continue monitoring without spamming you
- Resume normal operation when access is restored

### Want to test without waiting?

Temporarily reduce the sleep interval in `scraper.py`:
```python
time.sleep(random.randint(10, 20))  # Test mode: 10-20 seconds
```

## Features & Benefits vs Telegram

✅ **Simpler Setup** - No bot creation needed, just two API keys
✅ **Better Mobile Experience** - Native app notifications with rich formatting
✅ **Priority Levels** - Control notification importance
✅ **HTML Formatting** - Better looking messages with links
✅ **No Async Complexity** - Cleaner, more maintainable code
✅ **Reliable Delivery** - Pushover's proven notification infrastructure

## Contributing

Feel free to open issues or submit a pull request to improve the project!

## License

MIT License - Feel free to use and modify as needed.
