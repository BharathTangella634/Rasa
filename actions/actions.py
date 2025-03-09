
import pymongo
import google.generativeai as genai
from rasa_sdk import Action, Tracker
from rasa_sdk.executor import CollectingDispatcher
from rasa_sdk.events import SlotSet
import pytz
from datetime import datetime, timedelta

import schedule
import smtplib
import threading
import time
from email.mime.text import MIMEText
from db_config import users_collection, events_collection  # Import MongoDB connection

import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Access the keys
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")


# Configure Gemini API
genai.configure(api_key=GEMINI_API_KEY)

model = genai.GenerativeModel("gemini-1.5-flash")  # Use flash instead of pro

# MongoDB Connection
MONGO_URI = os.getenv("MONGO_URI")
DATABASE_NAME = "event_bot"

client = pymongo.MongoClient(MONGO_URI)
db = client[DATABASE_NAME]
users_collection = db["users"]
events_collection = db["events"]




def is_user_registered(user_id):
    user = users_collection.find_one({"teckzite_id": user_id})
    return user is not None  # Returns True if user exists


def send_email(to_email, event_name, event_time, event_location):
    sender_email = "tangellabharath143@gmail.com"  # Replace with your email
    sender_password = os.getenv("SENDER_PASSWORD")
    
    subject = f"Upcoming Event Reminder: {event_name}"
    body = f"Hello,\n\nYour event '{event_name}' is starting soon!\n\n📅 Time: {event_time}\n📍 Location: {event_location}\n\nSee you there!"

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = sender_email
    msg["To"] = to_email

    try:
        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(sender_email, sender_password)
        server.sendmail(sender_email, to_email, msg.as_string())
        server.quit()
        print(f"✅ Email sent to {to_email}")
    except Exception as e:
        print(f"❌ Failed to send email: {e}")



# Define Asia/Kolkata timezone
LOCAL_TIMEZONE = pytz.timezone("Asia/Kolkata")

def check_events():
    """Check for upcoming events and send reminder emails exactly 0-60 seconds before the event."""
    # Get the current local time in Asia/Kolkata (without microseconds)
    current_time_local = datetime.now(LOCAL_TIMEZONE).replace(microsecond=0)
    one_min_later = (current_time_local + timedelta(seconds=60)).replace(microsecond=0)

    # print(f"🔍 DEBUG: Checking for events between {current_time_local} and {one_min_later}")

    # Fetch all events
    all_events = list(events_collection.find())

    # print("\n📌 DEBUG: Listing all stored event timestamps in MongoDB:")
    upcoming_events = []

    for event in all_events:
        # Ensure event_time is a datetime object
        if isinstance(event["event_time"], str):
            event_time_obj = datetime.strptime(event["event_time"], '%Y-%m-%d %H:%M:%S%z')
        else:
            event_time_obj = event["event_time"]

        # Convert event_time to Asia/Kolkata timezone
        event_time_local = event_time_obj.astimezone(LOCAL_TIMEZONE).replace(microsecond=0)

        # print(f"➡️ {event['event_name']} at {event_time_local} (Stored Type: {type(event['event_time'])})")

        # Compare event time with the current time (0-60 seconds range)
        if current_time_local <= event_time_local <= one_min_later:
            upcoming_events.append(event)

    # Debugging: Check if any events are found
    # print(f"\n🔍 DEBUG: Found {len(upcoming_events)} upcoming events")

    # if not upcoming_events:
    #     print("⚠️ No events found in the next 60 seconds.")
    #     return

    for event in upcoming_events:
        # Use the exact date and time from MongoDB
        event_time_str = event["event_time"]  # No modifications
        # print(f"📅 Sending reminder for event: {event['event_name']} at {event_time_str}")

        # Fetch registered users
        registered_users = list(users_collection.find({"registered_events": event["event_id"]}))

        for user in registered_users:
            try:
                send_email(user["email"], event["event_name"], event_time_str, event["event_location"])
                # print(f"✅ Email sent to {user['email']} for event {event['event_name']}.")
            except Exception as e:
                print(f"❌ Failed to send email to {user['email']}: {e}")


def schedule_email_notifications():
    schedule.every(1).minutes.do(check_events)
    while True:
        schedule.run_pending()
        time.sleep(60)  # Runs every minute


threading.Thread(target=schedule_email_notifications, daemon=True).start()


class ActionGeneratePersonalizedResponse(Action):
    def name(self):
        return "action_generate_personalized_response"

    def run(self, dispatcher, tracker, domain):
        user_query = tracker.latest_message.get("text")
        intent = tracker.latest_message.get("intent").get("name")
        user_details = tracker.get_slot("user_details")  # This slot should contain teckzite_id
        print("User details",user_details)
        # If user details slot is empty, ask the user for their ID
        if not user_details:
            dispatcher.utter_message("I need your Teckzite ID to assist you. Please provide it.")
            return []

        # Fetch user details from MongoDB
        user_data = users_collection.find_one({"teckzite_id": user_details})
        
        print("user data",user_data)
        if not user_data:
            dispatcher.utter_message("I couldn't find your details in the database. Please register.")
            return []

        # Extract user details
        user_name = user_data.get("name", "User")
        user_email = user_data.get("email", "No email available")
        registered_events = user_data.get("registered_events", [])

        # Format event list as a string
        events_text = ", ".join(registered_events) if registered_events else "No events registered"

        # Get predefined response (if available)
        predefined_responses = domain.get("responses", {})
        default_response = predefined_responses.get(f"utter_{intent}", [{"text": ""}])[0]["text"]

        # Construct Gemini prompt
        prompt = f"""
        User details:
        Name: {user_name}
        Email: {user_email}
        Registered Events: {events_text}

        Query: {user_query}
        Default Response: {default_response}

        Generate a response considering the user's details.
        """

        # Generate response using Gemini
        response = model.generate_content(prompt)
        bot_response = response.text
        personalized_response = bot_response

        # Send response
        dispatcher.utter_message(personalized_response)
        return []
