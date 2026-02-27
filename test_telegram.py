import asyncio
import os
import httpx

async def send_telegram(message: str):
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    
    if not bot_token or not chat_id:
        print("❌ Telegram not configured - check environment variables")
        return
        
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "Markdown"
    }
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload, timeout=10)
            if response.status_code == 200:
                print("✅ Telegram message sent successfully!")
            else:
                print(f"❌ Telegram error: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"❌ Telegram connection error: {e}")

async def main():
    await send_telegram("🚀 Polymarket Bot Telegram Integration Working!")

if __name__ == "__main__":
    asyncio.run(main())
