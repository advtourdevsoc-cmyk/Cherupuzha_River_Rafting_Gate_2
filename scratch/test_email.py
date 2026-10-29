import os
import smtplib
from email.mime.text import MIMEText

def test_email():
    sender_email = "kuriankurian405@gmail.com"
    sender_password = "zqvo wclz rpzx ehti"
    recipient_email = "kuriankurian405@gmail.com" # Send to self
    
    msg = MIMEText("This is a test email from the Raft Booking app.")
    msg['Subject'] = 'Test Email'
    msg['From'] = f"River Rafting <{sender_email}>"
    msg['To'] = recipient_email
    
    try:
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.set_debuglevel(1)
        server.starttls()
        server.login(sender_email, sender_password)
        server.sendmail(sender_email, recipient_email, msg.as_string())
        server.quit()
        print("Test email sent completely successfully.")
    except Exception as e:
        print(f"Failed to send: {e}")

if __name__ == '__main__':
    test_email()
