import os
import json
import requests
from datetime import datetime, timedelta
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from PyPDF2 import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from io import BytesIO
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from dotenv import load_dotenv
import logging

# Load environment variables
load_dotenv()

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('automation.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class Config:
    """Configuration management"""
    GOOGLE_FORMS_ID = os.getenv('GOOGLE_FORMS_ID')
    WORKFLOW_TRIGGER_URL = os.getenv('WORKFLOW_TRIGGER_URL')
    CREDENTIALS_FILE = os.getenv('CREDENTIALS_FILE', 'credentials.json')
    TEMPLATE_PDF = os.getenv('TEMPLATE_PDF', 'CCM_Pre-Approval.pdf')
    SMTP_SERVER = os.getenv('SMTP_SERVER', 'smtp.gmail.com')
    SMTP_PORT = int(os.getenv('SMTP_PORT', 587))
    EMAIL_USER = os.getenv('EMAIL_USER')
    EMAIL_PASSWORD = os.getenv('EMAIL_PASSWORD')
    ADMIN_EMAIL = os.getenv('ADMIN_EMAIL')
    MICHAEL_EMAIL = os.getenv('MICHAEL_EMAIL')
    
    @classmethod
    def validate(cls):
        """Validate required configuration"""
        required = [
            'GOOGLE_FORMS_ID', 'WORKFLOW_TRIGGER_URL', 
            'EMAIL_USER', 'EMAIL_PASSWORD', 'ADMIN_EMAIL', 'MICHAEL_EMAIL'
        ]
        missing = [field for field in required if not getattr(cls, field)]
        
        if missing:
            raise ValueError(f"Missing required configuration: {', '.join(missing)}")
        
        if not os.path.exists(cls.CREDENTIALS_FILE):
            raise FileNotFoundError(f"Credentials file not found: {cls.CREDENTIALS_FILE}")
        
        if not os.path.exists(cls.TEMPLATE_PDF):
            raise FileNotFoundError(f"PDF template not found: {cls.TEMPLATE_PDF}")


class MortgageAutomation:
    def __init__(self):
        Config.validate()
        self.credentials = self.setup_google_credentials()
        self.processed_ids = self.load_processed_ids()
        
    def load_processed_ids(self):
        """Load previously processed response IDs"""
        if os.path.exists('processed_responses.json'):
            with open('processed_responses.json', 'r') as f:
                return set(json.load(f))
        return set()
    
    def save_processed_id(self, response_id):
        """Save processed response ID"""
        self.processed_ids.add(response_id)
        with open('processed_responses.json', 'w') as f:
            json.dump(list(self.processed_ids), f)
        
    def setup_google_credentials(self):
        """Setup Google API credentials"""
        try:
            SCOPES = ['https://www.googleapis.com/auth/forms.responses.readonly']
            creds = service_account.Credentials.from_service_account_file(
                Config.CREDENTIALS_FILE, scopes=SCOPES)
            logger.info("Google credentials loaded successfully")
            return creds
        except Exception as e:
            logger.error(f"Failed to load Google credentials: {e}")
            raise
    
    def fetch_form_responses(self):
        """Fetch responses from Google Forms"""
        try:
            service = build('forms', 'v1', credentials=self.credentials)
            
            # Get form to understand structure
            form = service.forms().get(formId=Config.GOOGLE_FORMS_ID).execute()
            question_map = {}
            
            for item in form.get('items', []):
                question_id = item.get('questionItem', {}).get('question', {}).get('questionId')
                title = item.get('title', '').lower()
                if question_id:
                    question_map[question_id] = title
            
            # Get responses
            result = service.forms().responses().list(
                formId=Config.GOOGLE_FORMS_ID
            ).execute()
            
            responses = result.get('responses', [])
            logger.info(f"Fetched {len(responses)} form response(s)")
            
            return self.parse_responses(responses, question_map)
            
        except HttpError as error:
            logger.error(f'Google Forms API error: {error}')
            return []
        except Exception as e:
            logger.error(f'Unexpected error fetching responses: {e}')
            return []
    
    def parse_responses(self, responses, question_map):
        """Parse form responses into structured data"""
        parsed_data = []
        
        for response in responses:
            response_id = response.get('responseId')
            
            # Skip for now!!!!!!!!!!
            if response_id in self.processed_ids:
                logger.info(f"Skipping already processed response: {response_id}")
                continue
            
            answers = response.get('answers', {})
            data = {
                'response_id': response_id,
                'timestamp': response.get('createTime'),
                'name': '',
                'address': '',
                'purchase_price': 0,
                'down_payment_type': '',
                'down_payment_value': 0,
                'down_payment_amount': 0,
                'property_type': '',
                'loan_type': ''
            }
            
            try:
                # Parse answers based on question mapping
                for question_id, answer in answers.items():
                    question_title = question_map.get(question_id, '').lower()
                    text_answers = answer.get('textAnswers', {}).get('answers', [])
                    
                    if not text_answers:
                        continue
                    
                    text_answer = text_answers[0].get('value', '')
                    
                    # Map based on question title
                    if 'name' in question_title and 'down' not in question_title:
                        data['name'] = text_answer
                    elif 'address' in question_title:
                        data['address'] = text_answer
                    elif 'purchase' in question_title and 'price' in question_title:
                        data['purchase_price'] = float(text_answer.replace(',', '').replace('$', ''))
                    elif 'how would you like' in question_title or 'enter your down payment' in question_title:
                        data['down_payment_type'] = text_answer
                    elif 'down payment (%)' in question_title:
                        data['down_payment_value'] = float(text_answer)
                    elif 'down payment ($)' in question_title:
                        data['down_payment_amount'] = float(text_answer.replace(',', '').replace('$', ''))
                    elif 'property type' in question_title:
                        data['property_type'] = text_answer
                    elif 'loan type' in question_title:
                        data['loan_type'] = text_answer
                
                # Calculate based on down payment type
                if data['down_payment_value'] > 0 and data['purchase_price'] > 0:
                    data['down_payment_amount'] = data['purchase_price'] * (data['down_payment_value'] / 100)
                elif data['down_payment_amount'] > 0 and data['purchase_price'] > 0:
                    data['down_payment_value'] = (data['down_payment_amount'] / data['purchase_price']) * 100
                
                # Calculate loan amount and LTV
                data['loan_amount'] = data['purchase_price'] - data['down_payment_amount']
                data['ltv'] = (data['loan_amount'] / data['purchase_price']) * 100 if data['purchase_price'] > 0 else 0
                
                # Validate essential data
                if data['name'] and data['purchase_price'] > 0:
                    parsed_data.append(data)
                    logger.info(f"Parsed response for: {data['name']}")
                else:
                    logger.warning(f"Incomplete data for response {response_id}")
                    
            except Exception as e:
                logger.error(f"Error parsing response {response_id}: {e}")
                continue
        
        return parsed_data
    
    def send_to_workflow(self, data):
        """Send data to workflow trigger and get response"""
        try:
            payload = {
                'name': data['name'],
                'address': data['address'],
                'purchase_price': data['purchase_price'],
                'loan_amount': data['loan_amount'],
                'down_payment': data['down_payment_amount'],
                'property_type': data['property_type'],
                'loan_type': data['loan_type'],
                'timestamp': datetime.now().isoformat()
            }
            
            logger.info(f"Sending to workflow: {data['name']}")
            response = requests.post(
                Config.WORKFLOW_TRIGGER_URL,
                json=payload,
                timeout=30
            )
            
            if response.status_code == 200:
                workflow_data = response.json()
                logger.info(f"Workflow response received: in_pipeline={workflow_data.get('in_pipeline', False)}")
                return {
                    'in_pipeline': workflow_data.get('in_pipeline', False),
                    'client_email': workflow_data.get('client_email', ''),
                    'realtor_email': workflow_data.get('realtor_email', '')
                }
            else:
                logger.error(f"Workflow trigger failed: {response.status_code} - {response.text}")
                return {'in_pipeline': False, 'client_email': '', 'realtor_email': ''}
                
        except requests.exceptions.Timeout:
            logger.error("Workflow request timed out")
            return {'in_pipeline': False, 'client_email': '', 'realtor_email': ''}
        except Exception as e:
            logger.error(f"Error sending to workflow: {e}")
            return {'in_pipeline': False, 'client_email': '', 'realtor_email': ''}
    
    def create_overlay_pdf(self, data):
        """Create overlay PDF with form data"""
        packet = BytesIO()
        can = canvas.Canvas(packet, pagesize=letter)
        
        # Calculate valid through date (90 days from now)
        valid_date = (datetime.now() + timedelta(days=90)).strftime('%m/%d/%Y')
        
        # Set font
        can.setFont("Helvetica", 10)
        
        # These positions are approximate - adjust based on your actual PDF
        # You may need to experiment with coordinates
        
        # Valid date (top right area)
        can.drawString(420, 680, valid_date)
        
        # Loan details section (adjust Y coordinates as needed)
        y_start = 580
        y_spacing = 20
        
        # Purchase Price
        can.drawString(420, y_start, f"${data['purchase_price']:,.2f}")
        
        # Loan Amount
        can.drawString(420, y_start - y_spacing, f"${data['loan_amount']:,.2f}")
        
        # Down Payment
        can.drawString(420, y_start - (2 * y_spacing), f"${data['down_payment_amount']:,.2f}")
        
        # Loan Type & Term
        can.drawString(420, y_start - (3 * y_spacing), data['loan_type'])
        
        # Loan-to-Value
        can.drawString(420, y_start - (5 * y_spacing), f"{data['ltv']:.2f}%")
        
        # Property Type
        can.drawString(420, y_start - (7 * y_spacing), data['property_type'])
        
        can.save()
        packet.seek(0)
        
        return packet
    
    def update_pdf(self, data):
        """Update PDF template with form data"""
        try:
            # Read template PDF
            template_pdf = PdfReader(Config.TEMPLATE_PDF)
            overlay_pdf = PdfReader(self.create_overlay_pdf(data))
            
            # Create writer
            output = PdfWriter()
            
            # Merge pages
            for page_num in range(len(template_pdf.pages)):
                page = template_pdf.pages[page_num]
                if page_num == 0:  # Add overlay to first page
                    page.merge_page(overlay_pdf.pages[0])
                output.add_page(page)
            
            # Save to bytes
            output_stream = BytesIO()
            output.write(output_stream)
            output_stream.seek(0)
            
            logger.info(f"PDF updated successfully for {data['name']}")
            return output_stream
            
        except Exception as e:
            logger.error(f"Error updating PDF: {e}")
            return None
    
    def create_email_body(self, data, workflow_response):
        """Create HTML email body"""
        pipeline_status = "In Pipeline" if workflow_response['in_pipeline'] else " Pending Review"
        pipeline_color = "#48bb78" if workflow_response['in_pipeline'] else "#ed8936"
        
        html = f"""
        <!DOCTYPE html>
        <html>
            <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333; margin: 0; padding: 0; background-color: #f5f5f5;">
                <div style="max-width: 650px; margin: 20px auto; background-color: #ffffff; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1);">
                    <!-- Header -->
                    <div style="background: linear-gradient(135deg, #2c5282 0%, #2d3748 100%); color: white; padding: 30px; border-radius: 10px 10px 0 0;">
                        <h1 style="margin: 0; font-size: 24px; font-weight: 600;">New Pre-Approval Request</h1>
                        <p style="margin: 10px 0 0 0; opacity: 0.9; font-size: 14px;">CrossCountry Mortgage Automation</p>
                    </div>
                    
                    <!-- Pipeline Status Badge -->
                    <div style="padding: 20px 30px; background-color: #f7fafc; border-bottom: 1px solid #e2e8f0;">
                        <div style="display: inline-block; padding: 8px 16px; background-color: {pipeline_color}; color: white; border-radius: 20px; font-weight: 600; font-size: 14px;">
                            {pipeline_status}
                        </div>
                    </div>
                    
                    <!-- Main Content -->
                    <div style="padding: 30px;">
                        <!-- Client Information -->
                        <div style="margin-bottom: 25px;">
                            <h2 style="color: #2d3748; font-size: 18px; margin: 0 0 15px 0; padding-bottom: 10px; border-bottom: 2px solid #e2e8f0;">
                                Client Information
                            </h2>
                            <table style="width: 100%; border-collapse: collapse;">
                                <tr>
                                    <td style="padding: 10px 0; color: #718096; font-weight: 600;">Name:</td>
                                    <td style="padding: 10px 0; color: #2d3748;">{data['name']}</td>
                                </tr>
                                <tr>
                                    <td style="padding: 10px 0; color: #718096; font-weight: 600;">Property Address:</td>
                                    <td style="padding: 10px 0; color: #2d3748;">{data['address']}</td>
                                </tr>
                            </table>
                        </div>
                        
                        <!-- Loan Details -->
                        <div style="margin-bottom: 25px;">
                            <h2 style="color: #2d3748; font-size: 18px; margin: 0 0 15px 0; padding-bottom: 10px; border-bottom: 2px solid #e2e8f0;">
                                Loan Details
                            </h2>
                            <table style="width: 100%; border-collapse: collapse;">
                                <tr style="background-color: #f7fafc;">
                                    <td style="padding: 12px; color: #718096; font-weight: 600; border-bottom: 1px solid #e2e8f0;">Purchase Price:</td>
                                    <td style="padding: 12px; color: #2d3748; font-weight: 600; border-bottom: 1px solid #e2e8f0; text-align: right;">${data['purchase_price']:,.2f}</td>
                                </tr>
                                <tr>
                                    <td style="padding: 12px; color: #718096; font-weight: 600; border-bottom: 1px solid #e2e8f0;">Down Payment:</td>
                                    <td style="padding: 12px; color: #2d3748; border-bottom: 1px solid #e2e8f0; text-align: right;">
                                        ${data['down_payment_amount']:,.2f} <span style="color: #718096;">({data['down_payment_value']:.1f}%)</span>
                                    </td>
                                </tr>
                                <tr style="background-color: #f7fafc;">
                                    <td style="padding: 12px; color: #718096; font-weight: 600; border-bottom: 1px solid #e2e8f0;">Loan Amount:</td>
                                    <td style="padding: 12px; color: #2d3748; font-weight: 600; border-bottom: 1px solid #e2e8f0; text-align: right;">${data['loan_amount']:,.2f}</td>
                                </tr>
                                <tr>
                                    <td style="padding: 12px; color: #718096; font-weight: 600; border-bottom: 1px solid #e2e8f0;">Loan-to-Value (LTV):</td>
                                    <td style="padding: 12px; color: #2d3748; border-bottom: 1px solid #e2e8f0; text-align: right;">{data['ltv']:.2f}%</td>
                                </tr>
                                <tr style="background-color: #f7fafc;">
                                    <td style="padding: 12px; color: #718096; font-weight: 600; border-bottom: 1px solid #e2e8f0;">Property Type:</td>
                                    <td style="padding: 12px; color: #2d3748; border-bottom: 1px solid #e2e8f0; text-align: right;">{data['property_type']}</td>
                                </tr>
                                <tr>
                                    <td style="padding: 12px; color: #718096; font-weight: 600;">Loan Type:</td>
                                    <td style="padding: 12px; color: #2d3748; text-align: right;">{data['loan_type']}</td>
                                </tr>
                            </table>
                        </div>
                        
                        <!-- Submission Info -->
                        <div style="background-color: #edf2f7; padding: 15px; border-radius: 8px; border-left: 4px solid #4299e1;">
                            <p style="margin: 0; color: #2d3748; font-size: 14px;">
                                <strong>📅 Submitted:</strong> {datetime.now().strftime('%B %d, %Y at %I:%M %p')}
                            </p>
                        </div>
                    </div>
                    
                    <!-- Footer -->
                    <div style="background-color: #f7fafc; padding: 20px 30px; border-radius: 0 0 10px 10px; border-top: 1px solid #e2e8f0;">
                        <p style="margin: 0; color: #718096; font-size: 13px; line-height: 1.5;">
                            📎 <strong>Attachment:</strong> Complete pre-approval letter with all details<br>
                            🔒 This email contains confidential information
                        </p>
                    </div>
                </div>
            </body>
        </html>
        """
        return html
    
    def send_email(self, data, workflow_response, pdf_stream):
        """Send email with PDF attachment"""
        try:
            # Create message
            msg = MIMEMultipart('alternative')
            msg['From'] = Config.EMAIL_USER
            msg['To'] = Config.ADMIN_EMAIL
            
            # Build CC list
            cc_list = [Config.MICHAEL_EMAIL]
            if workflow_response.get('client_email'):
                cc_list.append(workflow_response['client_email'])
            if workflow_response.get('realtor_email'):
                cc_list.append(workflow_response['realtor_email'])
            
            msg['Cc'] = ', '.join(cc_list)
            msg['Subject'] = f"🏠 Pre-Approval Request - {data['name']} - ${data['purchase_price']:,.0f}"
            
            # Email body
            html_body = self.create_email_body(data, workflow_response)
            msg.attach(MIMEText(html_body, 'html'))
            
            # Attach PDF
            if pdf_stream:
                part = MIMEBase('application', 'pdf')
                part.set_payload(pdf_stream.read())
                encoders.encode_base64(part)
                filename = f"PreApproval_{data['name'].replace(' ', '_')}_{datetime.now().strftime('%Y%m%d')}.pdf"
                part.add_header('Content-Disposition', f'attachment; filename="{filename}"')
                msg.attach(part)
            
            # Send email
            with smtplib.SMTP(Config.SMTP_SERVER, Config.SMTP_PORT) as server:
                server.starttls()
                server.login(Config.EMAIL_USER, Config.EMAIL_PASSWORD)
                
                recipients = [Config.ADMIN_EMAIL] + cc_list
                server.sendmail(Config.EMAIL_USER, recipients, msg.as_string())
            
            logger.info(f"Email sent successfully to {len(recipients)} recipient(s)")
            return True
            
        except smtplib.SMTPAuthenticationError:
            logger.error("SMTP Authentication failed. Check EMAIL_USER and EMAIL_PASSWORD")
            return False
        except Exception as e:
            logger.error(f"Error sending email: {e}")
            return False
    
    def process_submission(self, data):
        """Process a single form submission"""
        logger.info(f"\n{'='*60}")
        logger.info(f"Processing: {data['name']} - ${data['purchase_price']:,.2f}")
        logger.info(f"{'='*60}")
        
        try:
            # Send to workflow and get response
            workflow_response = self.send_to_workflow(data)
            
            # Update PDF
            pdf_stream = self.update_pdf(data)
            
            if not pdf_stream:
                logger.error("Failed to generate PDF")
                return False
            
            # Send email
            success = self.send_email(data, workflow_response, pdf_stream)
            
            if success:
                # Mark as processed
                self.save_processed_id(data['response_id'])
                logger.info(f"✅ Successfully processed {data['name']}")
                return True
            else:
                logger.error(f"❌ Failed to send email for {data['name']}")
                return False
                
        except Exception as e:
            logger.error(f"Error processing submission: {e}")
            return False
    
    def run(self):
        """Main execution method"""
        logger.info("="*60)
        logger.info("Starting Mortgage Pre-Approval Automation")
        logger.info("="*60)
        
        try:
            # Fetch form responses
            responses = self.fetch_form_responses()
            
            if not responses:
                logger.info("No new submissions to process")
                return
            
            logger.info(f"Found {len(responses)} new submission(s)")
            
            # Process each response
            successful = 0
            failed = 0
            
            for data in responses:
                if self.process_submission(data):
                    successful += 1
                else:
                    failed += 1
            
            # Summary
            logger.info("\n" + "="*60)
            logger.info(f"Processing Complete - Success: {successful}, Failed: {failed}")
            logger.info("="*60)
            
        except Exception as e:
            logger.error(f"Fatal error in automation: {e}")
            raise


if __name__ == "__main__":
    try:
        automation = MortgageAutomation()
        automation.run()
    except Exception as e:
        logger.error(f"Application failed to start: {e}")
        exit(1)