# Agentic AI Travel Planner

A class-based Streamlit travel assistant using Amazon Bedrock tool use, SerpApi,
and Geoapify.

## What it does

- Searches outbound and return flights with SerpApi Google Flights.
- Searches hotels with SerpApi Google Hotels.
- Finds restaurants near the destination with Geoapify Places.
- Uses Amazon Bedrock Converse tool use to decide when to call each tool.
- Produces a day-by-day itinerary and budget breakdown in the chat.

**Important:** SerpApi and Geoapify provide search information. This project
does not complete flight purchases or hotel reservations. The user must book
through the provider's checkout page.

## Project files

```text
travel_agent_project/
├── agent.py
├── main.py
├── prompt.py
├── tools.py
├── requirements.txt
├── .env.example
└── README.md
```

## Setup on Windows PowerShell

1. Open the project folder.
2. Create and activate your Conda environment (or use an existing Python 3.10+ environment):

   ```powershell
   conda create -n travelbot python=3.11 -y
   conda activate travelbot
   ```

3. Install packages:

   ```powershell
   pip install -r requirements.txt
   ```

4. Copy `.env.example` to `.env` and add your real keys:

   ```powershell
   Copy-Item .env.example .env
   ```

5. In the AWS Console, enable access to the selected foundation model in
   Amazon Bedrock for the region configured in `AWS_REGION`. Make sure the IAM
   identity has permission to invoke the model, for example
   `bedrock:InvokeModel` and `bedrock:InvokeModelWithResponseStream` as needed
   by your account/policies. This app uses the Converse API.

6. Run the app:

   ```powershell
   streamlit run main.py
   ```

## Example request

> Plan a 4-day round trip from Hyderabad to Goa for 2 adults, departing
> 2026-12-10 and returning 2026-12-13. My total budget is ₹30,000.

Use future dates when testing live searches.

## Environment variables

- `SERPAPI_KEY`: SerpApi key for Google Flights and Google Hotels.
- `GEOAPIFY_KEY`: Geoapify key for geocoding and restaurant search.
- `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`: AWS credentials. Boto3 can
  also use an AWS profile or an attached IAM role.
- `AWS_REGION`: Bedrock region, for example `us-east-1`.
- `BEDROCK_MODEL_ID`: defaults to `amazon.nova-lite-v1:0`.
- `SES_REGION_NAME`: included for a possible later email feature; it is not
  used by the current version because the current app does not send email.

Never commit `.env` to GitHub. Add it to `.gitignore`.

## Notes and limitations

- Airport names are resolved to IATA codes using the `airportsdata` package.
  If a city cannot be resolved, enter its three-letter airport code.
- Flight and hotel prices are indicative search results and may change.
- Restaurant opening hours and details should be checked before visiting.
- The model can only make a reliable budget comparison when the search results
  include usable prices. It should label missing items as estimates rather than
  inventing them.
- The initial version does not book tickets, reserve rooms, send emails, or
  charge a payment method.
