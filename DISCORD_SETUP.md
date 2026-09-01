# 🤖 Complete Discord Developer Portal Setup Guide

This step-by-step guide will walk you through creating your Discord Bot, generating your Bot Token, enabling required Gateway Intents, and inviting the bot to your server.

---

## Step 1: Create an Application in Discord Developer Portal

1. Open your browser and navigate to the **[Discord Developer Portal](https://discord.com/developers/applications)**.
2. Log in with your Discord account.
3. In the top right corner, click the blue **"New Application"** button.
4. Name your application (e.g., `MEXC Crypto Signal Bot`), check the Terms of Service box, and click **"Create"**.

---

## Step 2: Configure the Bot & Get Your Bot Token

1. In the left-hand sidebar menu, click on **"Bot"**.
2. **Username & Icon**: You can customize your bot's avatar and username here.
3. Under the **"Token"** section, click **"Reset Token"** (you may be asked for your 2FA code / password).
4. Click **"Copy"** to copy your new token.
5. Open your project folder and create a `.env` file (copied from `.env.example`).
6. Paste the token into `.env`:
   ```ini
   DISCORD_BOT_TOKEN=your_copied_token_here
   ```
   > ⚠️ **IMPORTANT**: Never share this token publicly or commit it to GitHub.

---

## Step 3: Enable Privileged Gateway Intents

Scroll down on the **"Bot"** page to the **"Privileged Gateway Intents"** section:

1. Turn **ON** `PRESENCE INTENT` (optional).
2. Turn **ON** `SERVER MEMBERS INTENT` (recommended).
3. Turn **ON** `MESSAGE CONTENT INTENT` (**REQUIRED** for bot commands).
4. Click **"Save Changes"** at the bottom of the page.

---

## Step 4: Generate the Bot Invite Link (OAuth2)

1. In the left-hand sidebar menu, click on **"OAuth2"** ➔ **"URL Generator"**.
2. Under **SCOPES**, check:
   - [x] `bot`
   - [x] `applications.commands` *(Essential for Slash Commands like `/analyze`)*
3. Scroll down to **BOT PERMISSIONS** and check:
   - [x] `Send Messages`
   - [x] `Send Messages in Threads`
   - [x] `Embed Links`
   - [x] `Attach Files`
   - [x] `Read Message History`
   - [x] `Use Slash Commands`
4. At the bottom of the page, copy the generated **"GENERATED URL"**.

---

## Step 5: Invite the Bot to Your Discord Server

1. Open a new browser tab and paste the **Generated URL**.
2. Select the Discord server you want to add the bot to.
3. Click **"Authorize"** and complete the CAPTCHA.
4. Your bot will now appear in your server's member list (offline until you run `python bot.py`).

---

## Step 6: (Optional) Set up an Automated Alerts Channel

If you want the bot to automatically broadcast high-probability crypto signals to a specific channel:

1. In Discord, go to **User Settings** ➔ **Advanced** ➔ Enable **Developer Mode**.
2. Right-click the channel you want signals posted in (e.g. `#crypto-signals`) and click **"Copy Channel ID"**.
3. Paste the ID into your `.env` file:
   ```ini
   ALERT_CHANNEL_ID=123456789012345678
   ```

---

## Step 7: Launch the Bot!

Run the bot from your terminal:
```powershell
python bot.py
```

Once running, you will see:
```text
Bot logged in as MEXC Crypto Signal Bot#1234
Synced 5 application slash command(s).
Auto-Scanner started (Interval: 5 min).
```

You can now use `/analyze`, `/chart`, `/scan`, and `/watchlist` directly inside Discord!
