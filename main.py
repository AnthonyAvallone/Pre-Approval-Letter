from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
import requests
import datetime
import smtplib
from email.message import EmailMessage
import mimetypes
import os
import glob
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from pypdf import PdfReader, PdfWriter
from io import BytesIO
from dotenv import load_dotenv


load_dotenv()

app = FastAPI()

WORKFLOW_WEBHOOK_URL = "https://services.leadconnectorhq.com/hooks/2iU49EVbjcmVqx5b5XNH/webhook-trigger/nzR1NVUzQofxbp0PtUUB"  


@app.post("/pre_approval/receive-form")
async def receive_form(request: Request):

    # In production use:
    data = await request.json()
    # For testing:
    #data = {
     #   'name': 'Anthony A Test',
      #  'address': '123 Main St',
       # 'purchasePrice': '350000',
       # 'downPaymentType': '$',
       # 'downPaymentPercent': '',
       # 'downPaymentAmount': '20000',
      #  'loanType': 'FHA',
     #   'propertyType': 'Condo'
    #}
    #     --- New Form Submission Received ---
    # {'name': 'Anthony A Test', 'address': '123, Main st', 'purchasePrice': '350000', 'downPaymentPercent': '', 'downPaymentAmount': '20000', 'propertyType': 'SFH'}

    print("\n--- New Form Submission Received ---")
    print(data)

    # Compute down payment
    down_payment = compute_down_payment(data)

    # Add computed value to data dict
    data["downPayment"] = down_payment

    # Generate PDF
    output_pdf_path = fill_pdf(data)

    # Send the data to your workflow webhook
    print("\n--- Sending to Webhook ---")
    print(WORKFLOW_WEBHOOK_URL)
    print("Payload:", data)

    response = requests.post(WORKFLOW_WEBHOOK_URL, json=data)

    print("\n--- Webhook Response ---")
    print("Status Code:", response.status_code)
    print("Response Body:", response.text)

    return {
        "status": "success",
        "pdf_path": output_pdf_path,
        "computed_down_payment": down_payment
    }


# ---------------------------
# DOWN PAYMENT CALCULATION
# ---------------------------

def compute_down_payment(data):
    purchase_price = float(data["purchasePrice"])

    # If user selected percentage (%)
    if data["downPaymentType"] == "%":
        percent = float(data["downPaymentPercent"])
        return f"{percent}%"  

    # If user selected amount ($)
    if data["downPaymentType"] == "$":
        amount = float(data["downPaymentAmount"])
        percent = (amount / purchase_price) * 100
        return f"{percent:.2f}%"

    return ""


def get_loan_amount(purchase_price, down_payment_percent):
    """
    purchase_price: float or string number
    down_payment_percent: float or string number (e.g. 20 for 20%)
    """
    purchase_price = float(purchase_price)
    down_payment_percent = float(down_payment_percent)

    # down payment = purchase price * (percent / 100)
    down_payment_amount = purchase_price * (down_payment_percent / 100)

    # loan amount = purchase price - down payment amount
    loan_amount = purchase_price - down_payment_amount

    return round(loan_amount, 2)


def get_LTV_value(down_payment_percent):
    """
    LTV = 100% - down payment %
    """
    down_payment_percent = float(down_payment_percent)
    LTV = 100 - down_payment_percent

    return f"{LTV:.2f}%"


# ---------------------------
# PDF FILLING FUNCTION WITH COORDINATES
# ---------------------------

def fill_pdf(data):
    template_path = "CCM Pre-Approval-template.pdf"
    output_path = f"approval_pdf/filled_preapproval_{datetime.datetime.now().timestamp()}.pdf"

    # Prepare replacement values
    today = datetime.datetime.now().strftime("%m/%d/%y")
    client_name = data.get('name', '')
    address = data.get('address', '')

    if data["downPaymentType"] == "%":
        dp_percent = float(data["downPaymentPercent"])
    else:
        dp_percent = (float(data["downPaymentAmount"]) / float(data["purchasePrice"])) * 100

    loan_type = data.get('loanType', '')
    purchase_price = f"${float(data.get('purchasePrice', 0)):,.2f}"

    loan_amount = get_loan_amount(data.get('purchasePrice', 0), dp_percent)
    data["loanAmount"] = loan_amount
    
    loan_amount_formatted = f"${loan_amount:,.2f}"
    
    
    property_type = data.get("propertyType", "")
    
    # Compute LTV
    ltv_value = get_LTV_value(dp_percent)
    data["LTV"] = ltv_value

    # Create a new PDF with reportlab to overlay text
    packet = BytesIO()
    can = canvas.Canvas(packet, pagesize=letter)
    
    # Set font
    can.setFont("Helvetica", 10)
    
    # Define coordinates for each field (x, y)
    # NOTE: PDF coordinates start from bottom-left corner
    # You'll need to adjust these coordinates based on your PDF layout
    # To find coordinates, open your PDF in a viewer and note positions
    
    # Date field (top right area)
    can.drawString(460, 695, today)
    
    # Client name and address (adjust these based on where they should appear)
    can.setFont("Helvetica-Bold", 10)
    can.drawString(30, 720, f"{client_name}")
    can.setFont("Helvetica", 9)
    can.drawString(30, 705, f"Site Name: {address}")
    
    # Purchase Price
    can.drawString(460, 610, purchase_price)
    
    # Loan Amount
    can.drawString(430, 595, loan_amount_formatted)
    
    # Down Payment
    can.drawString(420, 580, f"{dp_percent:.2f}%")
    
    # Loan Type & Term
    can.drawString(450, 565, loan_type)
    
    # Loan Amortization is already pre-filled as "30 YR FIXED"
    # can.drawString(450, 550, "30 YR FIXED")
    
    # Loan-to-Value
    can.drawString(430, 538, ltv_value)
    
    # Combined Loan-to-Value
    can.drawString(470, 522, ltv_value)
    
    # Property Type
    can.drawString(430, 508, property_type)
    
    # MLO Info (bottom section)
    can.setFont("Helvetica-Bold", 10)
    can.drawString(30, 270, "Anthony Avallone")
    can.setFont("Helvetica", 9)
    can.drawString(30, 255, "LOAN OFFICER, NMLS #2068208")
    can.drawString(30, 240, "Cell: 908-910-9276")
    can.drawString(30, 225, "Email: Anthony.Avallone@ccm.com")
    
    can.save()
    
    # Move to the beginning of the BytesIO buffer
    packet.seek(0)
    
    # Read the template PDF
    template_pdf = PdfReader(template_path)
    overlay_pdf = PdfReader(packet)
    
    # Merge the overlay with the template
    output = PdfWriter()
    
    # Get the first page of both PDFs
    template_page = template_pdf.pages[0]
    overlay_page = overlay_pdf.pages[0]
    
    # Merge the overlay onto the template
    template_page.merge_page(overlay_page)
    output.add_page(template_page)
    
    # Write the final PDF
    with open(output_path, "wb") as output_stream:
        output.write(output_stream)
    
    return output_path


# ---------------------------
# EMAILING FUNCTION
# ---------------------------

def send_email(subject, html_content, to_recipients, cc_recipients, pdf_path):
    sender = "Admin@anthonyavallonemortgages.com"
    
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = ", ".join(to_recipients)
    msg["Cc"] = ", ".join(cc_recipients)
    msg["Subject"] = subject
    
    msg.set_content("Your email client does not support HTML.")
    msg.add_alternative(html_content, subtype="html")

    # Attach PDF
    with open(pdf_path, "rb") as f:
        pdf_data = f.read()
    
    maintype, subtype = mimetypes.guess_type(pdf_path)[0].split("/")
    msg.add_attachment(pdf_data, maintype=maintype, subtype=subtype, filename=os.path.basename(pdf_path))

    # Combine To + CC for sending
    all_recipients = to_recipients + cc_recipients

    # Send via Gmail SMTP
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login("Admin@anthonyavallonemortgages.com", os.getenv("APP_PASSWORD"))
        smtp.send_message(msg, to_addrs=all_recipients)

def success_email_template(agent_email, client_email, data):
    return f"""
    <div style="font-family: Arial, sans-serif; line-height: 1.5; color: #000;">
        <!-- Congratulations Header -->
        <h2 style="color: #008080;">Congratulations {data.get('name', '')}</h2>
        <p>
            You have been pre-approved for a residential mortgage loan. 
            Attached is your Pre Approval Letter
        </p>

        <!-- Ready Section -->
        <div style="background-color: #0d2a5c; color: #ffffff; padding: 15px; margin-top: 20px;">
            <h3 style="margin-top:0;">We're ready when you are!</h3>
            <p>To continue your homebuying journey, we will need the following items for underwriting submission and review:</p>
            <ul style="margin-top: 10px;">
                <li><em>Signed loan application</em></li>
                <li><em>Fully executed sales contract</em></li>
                <li><em>Satisfactory appraisal ordered by CrossCountry Mortgage, LLC</em></li>
                <li><em>Documentation necessary to resolve any outstanding underwriting conditions after initial underwriting review</em></li>
                <li><em>Valid homeowners or condo insurance policy</em></li>
                <li><em>A satisfactory title commitment</em></li>
            </ul>
        </div>

        <!-- Footer -->
        <div style="margin-top: 30px; font-size: 14px; color: #333;">
            <p>Thank you for the opportunity to serve you for all of your home financing needs.</p>
            <p>Should you have any questions about this pre-approval letter or your application, please contact your loan officer:</p>
            <p>
                <strong>Anthony Avallone</strong><br>
                LOAN OFFICER, NMLS #2068208<br>
                Cell: 908-910-9276<br>
                Email: Anthony.Avallone@ccm.com
            </p>
        </div>
    </div>
    """

def failure_email_template(message):
    return f"""
    <div style="font-family: Arial; padding: 20px; background: #fff0f0;">
        <h2 style="color: #d9534f;">Pre-Approval Failed</h2>
        <p>The workflow returned an error:</p>
        <blockquote style="background:#ffecec;padding:10px;border-left:4px solid #d9534f;">
            {message}
        </blockquote>
        <p>The generated PDF is attached for review.</p>
        <p style="margin-top: 20px;">Regards,<br>Admin</p>
    </div>
    """


@app.post("/pre_approval/receive-data")
async def receive_workflow_data(request: Request):
    """
        Receives data from Webflow like:
        - {'success': 'True', 'Agent_email': 'agent@gmail.com', 'client_email': 'xxx@gmail.com'}
        - {'success': 'False', 'message': 'Client is not Found'}
        - {'success': 'False', 'message': 'Client is not in Pre Approval Pipeline'}
    """
    data = await request.json()
    
    print("\n--- New Webflow Data Received ---")
    print(data)

    # Path to latest PDF created by fill_pdf()
    pdf_files = glob.glob("approval_pdf/filled_preapproval_*.pdf")
    pdf_path = max(pdf_files, key=os.path.getctime) if pdf_files else None

    if not pdf_path:
        return {"error": "No PDF found. Make sure fill_pdf() ran before workflow callback."}

    # Condition A: success
    if str(data.get("success", "")).lower() == "true":
        agent = data.get("Agent_email")
        client = data.get("client_email")
        to_list = [agent, client]
        cc_list = ["Anthony.Avallone@ccm.com", "Michael.florio@ccm.com"]

        html = success_email_template(agent, client, data)
        send_email(
            subject="Pre-Approval Letter",
            html_content=html,
            to_recipients=to_list,
            cc_recipients=cc_list,
            pdf_path=pdf_path
        )

        return {"status": "Email sent (success)"}

    # Condition B: failure
    else:
        message = data.get("message", "Unknown error")

        to_list = ["Anthony.Avallone@ccm.com", "Michael.florio@ccm.com"]
        cc_list = []

        html = failure_email_template(message)

        send_email(
            subject="Pre-Approval Letter Failed",
            html_content=html,
            to_recipients=to_list,
            cc_recipients=cc_list,
            pdf_path=pdf_path
        )

        return {"status": "Email sent (failure)"}