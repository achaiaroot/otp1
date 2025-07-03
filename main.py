import os
import time
import re
import logging
import requests
import hashlib
import sys
from bs4 import BeautifulSoup
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('sms_monitor.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Configuration
BOT_TOKEN = "7966757311:AAFYJjbfPMBz39HIdFf9Cp3P2SFdxHcXzM4"
CHAT_ID = "-1002616225911"
BASE_URL = "http://51.89.99.105/NumberPanel"
LOGIN_URL = f"{BASE_URL}/login"
SMS_URL = f"{BASE_URL}/client/SMSCDRStats"
USERNAME = "Shakil05"
PASSWORD = "Shakil05"
CHECK_INTERVAL = 3  # seconds

# Country code mapping for better country detection
COUNTRY_CODES = {
    '1': 'United States',
    '44': 'United Kingdom',
    '91': 'India',
    '86': 'China',
    '49': 'Germany',
    '33': 'France',
    '39': 'Italy',
    '34': 'Spain',
    '81': 'Japan',
    '82': 'South Korea',
    '7': 'Russia',
    '55': 'Brazil',
    '52': 'Mexico',
    '61': 'Australia',
    '27': 'South Africa',
    '20': 'Egypt',
    '966': 'Saudi Arabia',
    '971': 'UAE',
    '90': 'Turkey',
    '31': 'Netherlands',
    '46': 'Sweden',
    '47': 'Norway',
    '45': 'Denmark',
    '41': 'Switzerland',
    '43': 'Austria',
    '32': 'Belgium',
    '351': 'Portugal',
    '30': 'Greece',
    '48': 'Poland',
    '420': 'Czech Republic',
    '36': 'Hungary',
    '40': 'Romania',
    '359': 'Bulgaria',
    '257': 'Burundi',  # Based on your data
    '256': 'Uganda',
    '254': 'Kenya',
    '255': 'Tanzania',
    '250': 'Rwanda',
    '992': 'Tajikistan',
}

# Global variables
sent_messages_hashes = set()
sent_messages_log = {}
last_sent_times = {}
session_active = False
driver = None
last_processed_timestamp = None  # Track last processed message timestamp

def load_sent_messages():
    """Load previously sent messages from log file"""
    global sent_messages_hashes, sent_messages_log, last_sent_times
    try:
        with open('sent_messages.log', 'r') as f:
            for line in f:
                parts = line.strip().split('|')
                if len(parts) >= 3:
                    msg_hash = parts[0]
                    timestamp = float(parts[1])
                    number = parts[2]
                    sent_messages_hashes.add(msg_hash)
                    sent_messages_log[msg_hash] = timestamp
                    last_sent_times[msg_hash] = timestamp
        logger.info(f"Loaded {len(sent_messages_hashes)} previous messages from log file")
    except FileNotFoundError:
        logger.info("No previous message log found, starting fresh")
    except Exception as e:
        logger.warning(f"Error loading message log: {e}")

def save_sent_message(msg_hash, timestamp, number):
    """Save sent message to log file"""
    try:
        with open('sent_messages.log', 'a') as f:
            f.write(f"{msg_hash}|{timestamp}|{number}\n")
    except Exception as e:
        logger.error(f"Error saving to log file: {e}")

def get_country_from_number(phone_number):
    """Get country name from phone number"""
    cleaned_number = re.sub(r'\D', '', str(phone_number))
    
    for code, country in COUNTRY_CODES.items():
        if cleaned_number.startswith(code):
            return country
    
    return "Unknown"

def extract_otp(message):
    """Extract OTP code from SMS message"""
    patterns = [
        r'G-(\d{6})',               # Google format
        r'(\d{6})\s*is your',       # "123456 is your"
        r'code:\s*(\d{4,8})',       # "code: 123456"
        r'verification code\s*[:\-]?\s*(\d{4,8})',  # "verification code: 123456"
        r'otp\s*[:\-]?\s*(\d{4,8})',               # "OTP: 123456"
        r'pin\s*[:\-]?\s*(\d{4,8})',               # "PIN: 1234"
        r'\b(\d{4,8})\b',           # Any 4-8 digit number
    ]
    
    for pattern in patterns:
        match = re.search(pattern, message, re.IGNORECASE)
        if match:
            return match.group(1)
    
    return "Not found"

def send_to_telegram(text):
    """Send message to Telegram"""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    try:
        response = requests.post(url, data=payload, timeout=30)
        if response.status_code == 200:
            logger.info("Message sent successfully to Telegram")
            return True
        elif response.status_code == 429:
            retry_after = response.json().get('parameters', {}).get('retry_after', 30)
            logger.warning(f"Rate limited. Waiting {retry_after} seconds...")
            time.sleep(retry_after + 1)
            response = requests.post(url, data=payload, timeout=30)
            if response.status_code == 200:
                logger.info("Message sent after rate limit wait")
                return True
            else:
                logger.error(f"Failed after retry: {response.text}")
                return False
        elif response.status_code == 400:
            if "not enough rights" in response.text:
                logger.critical("FATAL: Bot does not have permission to send messages. Please make bot admin in chat.")
                return "permission_denied"
            elif "chat not found" in response.text:
                logger.critical("FATAL: Chat not found. Please verify the CHAT_ID configuration.")
                return "chat_not_found"
            else:
                logger.error(f"Telegram error: {response.text}")
                return False
        else:
            logger.error(f"Telegram error: {response.text}")
            return False
    except Exception as e:
        logger.error(f"Telegram exception: {e}")
        return False

def solve_captcha(page_source):
    """Solve math captcha from login page"""
    try:
        captcha_match = re.search(r'What is (\d+) \+ (\d+)', page_source)
        if captcha_match:
            num1 = int(captcha_match.group(1))
            num2 = int(captcha_match.group(2))
            result = num1 + num2
            logger.info(f"Solved captcha: {num1} + {num2} = {result}")
            return str(result)
        else:
            # Try other captcha patterns
            captcha_patterns = [
                r'(\d+)\s*\+\s*(\d+)',
                r'(\d+)\s*-\s*(\d+)',
                r'(\d+)\s*\*\s*(\d+)',
                r'(\d+)\s*/\s*(\d+)'
            ]
            for pattern in captcha_patterns:
                match = re.search(pattern, page_source)
                if match:
                    num1, num2 = int(match.group(1)), int(match.group(2))
                    if '+' in pattern:
                        result = num1 + num2
                    elif '-' in pattern:
                        result = num1 - num2
                    elif '*' in pattern:
                        result = num1 * num2
                    elif '/' in pattern:
                        result = num1 // num2
                    logger.info(f"Solved captcha: {num1} operation {num2} = {result}")
                    return str(result)
        logger.warning("Could not find captcha pattern")
        return None
    except Exception as e:
        logger.error(f"Error solving captcha: {e}")
        return None

def init_driver():
    """Initialize Chrome WebDriver"""
    global driver
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option("useAutomationExtension", False)
    
    try:
        # Try to use system Chrome/Chromium
        driver = webdriver.Chrome(options=chrome_options)
    except Exception as e:
        logger.error(f"Failed to initialize Chrome driver: {e}")
        # Try with explicit paths if needed
        try:
            chrome_options.binary_location = "/nix/store/zi4f80l169xlmivz8vja8wlphq74qqk0-chromium-125.0.6422.141/bin/chromium"
            chrome_service = Service("/nix/store/3qnxr5x6gw3k9a9i7d0akz0m6bksbwff-chromedriver-125.0.6422.141/bin/chromedriver")
            driver = webdriver.Chrome(service=chrome_service, options=chrome_options)
        except Exception as e2:
            logger.error(f"Failed with explicit paths: {e2}")
            raise
    
    driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    logger.info("Chrome WebDriver initialized successfully")

def login():
    """Login to SMS panel"""
    global session_active, driver
    
    max_retries = 3
    for attempt in range(max_retries):
        try:
            driver.delete_all_cookies()
            driver.get(LOGIN_URL)
            logger.info(f"Login attempt {attempt + 1}/{max_retries}")
            
            wait = WebDriverWait(driver, 10)
            
            # Wait for and fill username
            username_field = wait.until(EC.presence_of_element_located((By.NAME, "username")))
            username_field.clear()
            username_field.send_keys(USERNAME)
            
            # Fill password
            password_field = driver.find_element(By.NAME, "password")
            password_field.clear()
            password_field.send_keys(PASSWORD)
            
            # Handle captcha
            page_source = driver.page_source
            captcha_result = solve_captcha(page_source)
            
            if captcha_result:
                try:
                    captcha_field = driver.find_element(By.NAME, "capt")
                    captcha_field.clear()
                    captcha_field.send_keys(captcha_result)
                    
                    # Submit form
                    captcha_field.send_keys(Keys.RETURN)
                    
                    # Wait for redirect or error
                    time.sleep(5)
                    
                    if "client" in driver.current_url or "dashboard" in driver.current_url:
                        session_active = True
                        logger.info("Login successful!")
                        send_to_telegram("✅ SMS Monitor Started!\n\n🔐 Successfully logged in to panel\n📱 Now monitoring for new SMS messages\n\nAuthor: Master x Torikul")
                        return True
                    else:
                        logger.warning(f"Login failed - current URL: {driver.current_url}")
                        if "login" in driver.current_url:
                            # Check for error messages
                            try:
                                error_elements = driver.find_elements(By.CLASS_NAME, "alert")
                                for error in error_elements:
                                    logger.warning(f"Login error: {error.text}")
                            except:
                                pass
                        continue
                except NoSuchElementException:
                    logger.warning("Captcha field not found")
                    continue
            else:
                logger.warning("Could not solve captcha")
                continue
                
        except Exception as e:
            logger.error(f"Login attempt {attempt + 1} failed: {e}")
            time.sleep(2)
    
    session_active = False
    send_to_telegram("⚠️ Login failed after multiple attempts. Please check credentials or server status.")
    return False

def check_session():
    """Check if session is still active"""
    global session_active
    try:
        driver.get(SMS_URL)
        time.sleep(3)
        if "login" in driver.current_url:
            session_active = False
            logger.info("Session expired, attempting to re-login")
            return login()
        return True
    except Exception as e:
        logger.error(f"Session check failed: {e}")
        session_active = False
        return False

def format_telegram_message(number, service, sms_text, timestamp):
    """Format futuristic Telegram message with enhanced features"""
    otp = extract_otp(sms_text)
    country = get_country_from_number(number)
    
    # Create clickable OTP code if found
    otp_display = f'<code>{otp}</code>' if otp != "Not found" else otp
    
    # Create Telegram profile link for Master
    master_link = "https://t.me/ogmstr"
    
    message = f"""💸 <b>OTP ALERT</b> 💸

🔢 <b>Number:</b> <code>{number}</code>
🔑 <b>OTP Code:</b> {otp_display}
🏷️ <b>Service:</b> {service}
🌎 <b>Country:</b> {country}

⏰ <b>Time:</b> {timestamp}
📩 <b>Message:</b>
{sms_text}

<b>Author:</b> <a href="{master_link}">𝗠𝗔$𝗧𝗘𝗥 </a>"""
    
    return message

def extract_otp(message):
    """Extract OTP code from SMS message with improved patterns"""
    patterns = [
        r'G-(\d{6})',               # Google format
        r'(\d{6})\s*is your',       # "123456 is your"
        r'code:\s*(\d{4,8})',       # "code: 123456"
        r'verification code\s*[:\-]?\s*(\d{4,8})',  # "verification code: 123456"
        r'otp\s*[:\-]?\s*(\d{4,8})',               # "OTP: 123456"
        r'pin\s*[:\-]?\s*(\d{4,8})',               # "PIN: 1234"
        r'WhatsApp code\s*(\d{3}-\d{3})',  # WhatsApp code 123-456
        r'code\s*(\d{3}-\d{3})',    # Generic code 123-456
        r'\b(\d{4,8})\b',           # Any 4-8 digit number
    ]
    
    for pattern in patterns:
        match = re.search(pattern, message, re.IGNORECASE)
        if match:
            return match.group(1)
    
    return "Not found"

def extract_sms():
    """Extract SMS messages from panel"""
    global sent_messages_hashes, session_active
    
    max_retries = 3
    for attempt in range(max_retries):
        try:
            # Check session
            if not session_active and not login():
                logger.warning("Session inactive and login failed")
                continue
            if not check_session():
                logger.warning("Session check failed")
                continue
            
            # Navigate to SMS stats page
            driver.get(SMS_URL)
            time.sleep(3)
            
            # Try to click "Show Report" button if it exists
            try:
                show_report_button = WebDriverWait(driver, 10).until(
                    EC.element_to_be_clickable((By.XPATH, "//button[contains(text(), 'Show Report')]"))
                )
                show_report_button.click()
                time.sleep(3)
                logger.info("Clicked Show Report button")
            except TimeoutException:
                logger.info("Show Report button not found or not needed")
            except Exception as e:
                logger.warning(f"Error clicking Show Report: {e}")
            
            # Find tables with SMS data
            tables = driver.find_elements(By.TAG_NAME, "table")
            if not tables:
                logger.warning("No tables found on SMS page")
                return
            
            new_messages_count = 0
            all_messages = []
            current_time = time.time()
            
            # Collect all messages from all tables
            for table in tables:
                try:
                    rows = table.find_elements(By.TAG_NAME, "tr")
                    if len(rows) <= 1:  # Skip tables with no data rows
                        continue
                    
                    # Check if this looks like the SMS table
                    headers = rows[0].find_elements(By.TAG_NAME, "th")
                    header_text = " ".join([h.text.lower() for h in headers])
                    
                    if not any(keyword in header_text for keyword in ['number', 'sms', 'cli', 'message']):
                        continue
                    
                    logger.info(f"Processing table with {len(rows)-1} rows")
                    
                    # Process each row in the table
                    for row in rows[1:]:  # Skip header row
                        try:
                            cells = row.find_elements(By.TAG_NAME, "td")
                            if len(cells) < 4:  # Need at least date, number, service, message
                                continue
                            
                            # Extract SMS data
                            date_time = cells[0].text.strip() if cells[0].text.strip() else datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                            number = cells[2].text.strip() if len(cells) > 2 else ""
                            service = cells[3].text.strip() if len(cells) > 3 else "Unknown"
                            sms_text = cells[4].text.strip() if len(cells) > 4 else ""
                            
                            if not sms_text or not number:
                                continue
                            
                            # Create unique message identifier
                            msg_content = f"{number}|{service}|{sms_text}|{date_time}"
                            msg_hash = hashlib.md5(msg_content.encode()).hexdigest()
                            
                            # Skip if already sent
                            if msg_hash in sent_messages_hashes:
                                continue
                            
                            # Skip if duplicate in last 5 minutes
                            is_duplicate = False
                            for existing_hash, timestamp in sent_messages_log.items():
                                if current_time - timestamp < 300:  # 5 minutes
                                    # Check if same number and similar message
                                    if number in existing_hash:
                                        similarity_score = len(set(sms_text.split()) & set(existing_hash.split()))
                                        if similarity_score >= 3:  # More strict similarity check
                                            is_duplicate = True
                                            break
                            if is_duplicate:
                                continue
                            
                            # Add message to collection
                            all_messages.append({
                                'number': number,
                                'service': service,
                                'message': sms_text,
                                'time': date_time,
                                'hash': msg_hash
                            })
                        
                        except Exception as e:
                            logger.error(f"Error processing row: {e}")
                            continue
                
                except Exception as e:
                    logger.error(f"Error processing table: {e}")
                    continue
            
            # Process all new messages
            if all_messages:
                # Sort messages by timestamp (oldest first to process in order)
                all_messages.sort(key=lambda x: x['time'])
                
                for msg in all_messages:
                    # Format and send message
                    telegram_message = format_telegram_message(
                        msg['number'], 
                        msg['service'], 
                        msg['message'], 
                        msg['time']
                    )
                    
                    # Try sending with retries
                    for send_attempt in range(3):
                        telegram_result = send_to_telegram(telegram_message)
                        if telegram_result == True:
                            # Mark as sent
                            sent_messages_hashes.add(msg['hash'])
                            sent_messages_log[msg['hash']] = current_time
                            save_sent_message(msg['hash'], current_time, msg['number'])
                            new_messages_count += 1
                            break
                        elif telegram_result in ["permission_denied", "chat_not_found"]:
                            return  # Exit immediately on critical errors
                        else:
                            logger.warning(f"Message send failed (attempt {send_attempt+1}/3). Retrying...")
                            time.sleep(2)
                    
                    # Small delay between messages
                    time.sleep(1)
            
            if new_messages_count > 0:
                logger.info(f"Successfully sent {new_messages_count} new messages to Telegram")
            else:
                logger.info("No new messages to send")
            
            # If we reached here, extraction was successful
            return
            
        except Exception as e:
            logger.error(f"SMS extraction attempt {attempt+1}/{max_retries} failed: {e}")
            session_active = False
            time.sleep(5)  # Wait before retrying
    
    logger.error("SMS extraction failed after multiple attempts")
    session_active = False

def cleanup():
    """Cleanup resources"""
    global driver
    try:
        if driver:
            driver.quit()
            logger.info("Driver closed successfully")
    except Exception as e:
        logger.error(f"Error closing driver: {e}")

def main():
    """Main function"""
    global session_active
    
    logger.info("🚀 Starting SMS Monitor Bot")
    logger.info(f"🎯 Target URL: {SMS_URL}")
    logger.info(f"👤 Username: {USERNAME}")
    
    # Load previous messages
    load_sent_messages()
    
    # Initialize driver
    try:
        init_driver()
    except Exception as e:
        logger.error(f"Failed to initialize driver: {e}")
        send_to_telegram(f"⚠️ SMS Monitor failed to start: {str(e)}")
        return
    
    try:
        # Initial login
        if not login():
            logger.error("Initial login failed")
            return
        
        failure_count = 0
        max_failures = 5
        
        # Main monitoring loop
        while True:
            try:
                extract_sms()
                failure_count = 0  # Reset on success
                time.sleep(CHECK_INTERVAL)
                
            except KeyboardInterrupt:
                logger.info("🛑 Bot stopped by user")
                send_to_telegram("🛑 SMS Monitor stopped by user\n\nAuthor: Master")
                break
                
            except Exception as e:
                logger.error(f"Main loop error: {e}")
                failure_count += 1
                
                if failure_count >= max_failures:
                    logger.error(f"Too many failures ({failure_count}), stopping bot")
                    send_to_telegram(f"⚠️ SMS Monitor stopped due to repeated failures: {str(e)}\n\nAuthor: Master")
                    break
                
                session_active = False
                time.sleep(CHECK_INTERVAL * 2)  # Wait longer on error
                
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        send_to_telegram(f"💥 SMS Monitor crashed: {str(e)}\n\nAuthor: Master")
    
    finally:
        cleanup()
        logger.info("🏁 SMS Monitor shutdown complete")

if __name__ == "__main__":
    main()
