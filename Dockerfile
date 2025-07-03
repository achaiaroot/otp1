FROM python:3.9-slim

# Install Chrome and ChromeDriver
RUN apt-get update && apt-get install -y chromium chromium-driver

# Copy and install Python dependencies
COPY requirements.txt .
RUN pip install -r requirements.txt

# Copy your script
COPY main.py .

# Run the script
CMD ["python", "main.py"]
