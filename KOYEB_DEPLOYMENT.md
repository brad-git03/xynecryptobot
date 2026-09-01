# 🚀 24/7 Cloud Deployment Guide (Koyeb & GitHub)

This guide walks you through deploying your **MEXC AI Discord Trading Bot** to **Koyeb** so it runs **24/7/365 for 100% FREE** without keeping your computer on.

---

## 📦 Step 1: Push Code to GitHub

Open PowerShell or your terminal in this project directory:

```bash
# 1. Initialize git (if not already initialized)
git init

# 2. Add all files
git add .

# 3. Commit your code
git commit -m "Initial commit of MEXC AI Trading Bot"

# 4. Create a new repository on GitHub (https://github.com/new)
# Name it e.g. "mexc-discord-trading-bot" and set it to PRIVATE.

# 5. Link your local repo to GitHub & push
git remote add origin https://github.com/YOUR_GITHUB_USERNAME/mexc-discord-trading-bot.git
git branch -M main
git push -u origin main
```

---

## ⚡ Step 2: Deploy for Free on Koyeb

1. **Sign Up / Log In**:
   * Go to **[https://www.koyeb.com](https://www.koyeb.com)** and sign in (you can sign in directly with GitHub).

2. **Create a New Service**:
   * Click the **"Create Service"** button in your Koyeb dashboard.
   * Select **GitHub** as the deployment source.
   * Choose your repository: `mexc-discord-trading-bot`.
   * Branch: `main`.

3. **Configure Service Type**:
   * Under **Service Type**, select **"Worker"** (or **"Web Service"** using Docker/Buildpack).
   * Under **Instance Size**, choose **"Nano" (Free Eco)**.

4. **Add Environment Variables**:
   Click **"Add Environment Variable"** and enter:
   * `DISCORD_BOT_TOKEN`: `Your_Actual_Discord_Bot_Token`
   * `ALERT_CHANNEL_ID`: `Your_Discord_Channel_ID` (optional, can also be configured inside Discord using `/setchannel`)

5. **Deploy**:
   * Click **"Deploy"** at the bottom right.

---

## 🟢 Step 3: Verify & Celebrate!

* Koyeb will automatically build the container and start `python bot.py`.
* Check the **Runtime Logs** in Koyeb: You will see:
  ```text
  Bot logged in as CryptoXyneBot
  Synced 23 application slash command(s).
  Auto MTF Scalp Scanner started.
  Active Trade Copilot Sentinel started.
  ```
* In Discord, type:
  ```text
  !radar
  ```
* **Done!** You can now safely close your browser and turn off your PC. The bot will run 24/7 in the cloud and automatically update whenever you push changes to GitHub!
