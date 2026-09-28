# Pantry Wizard

One shared pantry for the household, kept in a Google Sheet, and talked to through Grok Bot.

You do not open an app and tick boxes. You say what happened in the kitchen. The bot writes that down in a way the next trip can use. A second person can ask their own bot the night before a shop, and both of them are looking at the same sheet.

> “We’re out of Rice Krispies.”
>
> “I opened the last toothpaste.”
>
> “We usually buy ketchup at FoodMaxx.”
>
> “I bought two gallons of milk.”

“I used up a bottle of ketchup” does not silently put ketchup on the list. It means the bot should remember that you may want to check the pantry. The sheet stores what someone actually said. It does not pretend the house is a warehouse with a count on every jar.

Before you leave, ask:

> “I’m heading to FoodMaxx. What do I need to know?”

You get a short briefing: what was asked for, what is probably low, what is worth a look before you go, and what to skip because the house already has plenty.

If you shop at Costco, the same bot can read your warehouse receipts and today’s warehouse prices. A sale is Costco’s own promotion, the “$4 off until October 5” kind, with an end date when Costco sent one. A price that is merely lower than what you paid last time is not called a sale. The bot never uses that login to buy, check out, or place an order.

## What you get

- A private copy of the household workbook: items, recent store prices, and a history of what was said.
- A Grok Bot that reads and updates that workbook instead of trusting the last chat.
- Trip briefings that use the sheet, not a guess from memory.
- Optional Costco history for one warehouse you confirm. Other warehouses on the membership are skipped.
- A daily Costco check that stays quiet unless sign-in failed or something you actually buy went on sale.

The workbook is the memory. Chat is not. If the bot cannot open the sheet, it should say so and stop writing.

## Set up your own copy

You need a Google account and [Grok Bot](https://cursor.com/download), signed in.

### 1. Copy the template sheet

Open the template:

https://docs.google.com/spreadsheets/d/1_y9qlX15wQshsmXMEK9uGuXCzr7tS58lNc1y1mdkARs/edit?usp=sharing

Choose **File → Make a copy**. Name it something like “Pantry Wizard”. The copy is yours. Leave the template alone.

Copy the URL of **your** sheet from the address bar. That is the URL you will give the bot. If you paste the template link, the bot will try to edit a sheet it should not change.

### 2. Make a Sheets-only OAuth token

You create a token in your own Google Cloud project and store it on the bot. The token’s only scope is the Sheets API, so the bot calls `sheets.googleapis.com` for the spreadsheet id in your sheet URL.

Do this while signed in as the Google account that owns your copy. That account must be able to edit the sheet. If you mint the token as someone else, share the copy with that account as **Editor**.

1. In [Google Cloud Console](https://console.cloud.google.com/), create a project. Name it something like “Pantry Wizard”.
2. Enable the **Google Sheets API** for that project. Do not enable the Drive API.
3. Open **Google Auth platform → Audience**. Choose **External**. Add your own Google address under **Test users**.
4. Open **Data access** and add only this scope: `https://www.googleapis.com/auth/spreadsheets`. That scope reads and writes spreadsheets the signed-in account can edit.
5. Open **Clients → Create client**. Choose **Web application**. Under **Authorized redirect URIs** add `https://developers.google.com/oauthplayground`. Copy the client id and client secret.
6. Open the [OAuth 2.0 Playground](https://developers.google.com/oauthplayground/). Click the gear and turn on **Use your own OAuth credentials**. Paste the client id and client secret.
7. In the scope box, enter `https://www.googleapis.com/auth/spreadsheets`. Click **Authorize APIs**. Sign in as the account that can edit your copy. If Google says the app is unverified, choose **Advanced** and continue. It is your project.
8. Click **Exchange authorization code for tokens**. Copy the `refresh_token`. The bot exchanges it for a short-lived access token each time it calls the API.

While the consent screen stays in **Testing**, Google expires that refresh token after 7 days. For a bot that runs every day, set the audience to **In production**. You do not need Google to verify the app when the only user is you. You will still click through the unverified-app warning once.

### 3. Store the token on the bot

Do not paste the refresh token, client secret, or client id into the chat.

Open that Bot’s **Secrets** and add three secrets. The description is visible to the bot. The value is not. See [Store secrets securely](https://cursor.com/help/grok-bot/secrets).

| Name | What it is |
|---|---|
| `GOOGLE_CLIENT_ID` | The OAuth client id |
| `GOOGLE_CLIENT_SECRET` | The OAuth client secret |
| `GOOGLE_REFRESH_TOKEN` | The refresh token from the playground |

In each description, say that these are for the Google Sheets API only, and paste the URL of your sheet copy.

### 4. If the Sheets API refuses the bot

| What you see | What to do |
|---|---|
| `accessNotConfigured` or API has not been used | Enable the Google Sheets API on that Cloud project, wait a minute, and try again. |
| `redirect_uri_mismatch` | The OAuth client is missing `https://developers.google.com/oauthplayground` as a redirect URI. Add it, then authorize again. |
| `access_denied`, or Google will not show the consent screen | The app is in Testing and that Google account is not a test user. Add the address under Audience → Test users. |
| Unverified app | Choose Advanced and continue. This is your Cloud project, used by you. |
| `invalid_client` | The client id or client secret saved on the bot does not match the Cloud client. Replace the secret. You cannot read the old value back. |
| `invalid_grant` | The refresh token was revoked, expired, or minted for a different client. Testing-mode tokens die after 7 days. Run the playground again and replace `GOOGLE_REFRESH_TOKEN`. Put the app in production if this keeps happening. |
| `403` The caller does not have permission | The Google account that created the token cannot edit this sheet. Share your copy with that account as Editor, or mint a new token while signed in as the owner. |
| The bot says it cannot see the secret | Tell it the environment variable names above. Ask it to request a missing one with the secure secret card. Do not paste the value into the message. |

Revoke a token you pasted into chat, or one you want to throw away, at [Google Account permissions](https://myaccount.google.com/permissions). Then mint a new refresh token.

### 5. The prompt

Start a new chat with that bot and send this. Replace the sheet URL with your copy.

```text
Set up Pantry Wizard for this household.

Our workbook is this Google Sheet, our copy, not the template:
<paste the URL of your copy>

Read and write it with the Google Sheets API. Do not use the Google Drive or Google Sheets plugins.
The Bot secrets GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, and GOOGLE_REFRESH_TOKEN are a token I created in my own Google Cloud project.
Its only scope is https://www.googleapis.com/auth/spreadsheets.
Refresh an access token at https://oauth2.googleapis.com/token, then call https://sheets.googleapis.com for the spreadsheet id in that URL.
Do not print those secrets. If one is missing, ask for it with the secure secret card.

Load and follow these runtime instructions before you change anything:
https://raw.githubusercontent.com/esper256/shared-agentic-shopping-list/main/AGENT_RUNTIME_INSTRUCTIONS.md

If the sheet layout is unfamiliar, also load:
https://raw.githubusercontent.com/esper256/shared-agentic-shopping-list/main/GOOGLE_SHEETS_SCHEMA.md

Read Config, Items, RetailMemory, Integrations, and Events.
If household_timezone or currency is blank, ask me once and then set them.
If protocol_version or behavior_spec_version is older than the instructions you loaded, update those two Config cells. That is housekeeping. Do not ask me first.
Do not invent items, quantities, or prices.

When the instructions are actually loaded, say the activation line they require.
Then tell me you can read the sheet, and quote the protocol_version you see in Config.
```

### 6. How you know it is ready

The bot is ready when it says this exact line:

> Shopping data steward active — runtime instructions v0.8 loaded.

It should also quote `protocol_version` from your sheet’s Config tab. After this setup that value should be `0.8`. If the sheet was older, the bot updates it without asking. That is expected.

Try one ordinary sentence: “We’re out of Rice Krispies.” The bot should write that to the sheet, and you should be able to see the change. If it cannot open the sheet, it should say so and leave the sheet alone.

It is not ready if it skips the activation line, answers the trip question from chat memory, or adds items you never mentioned.

You do not need to clone this Git repository yourself. The bot loads the instructions from the links above.

## Add Costco

After the activation line, you can connect one Costco warehouse. In the same chat:

```text
Install the Costco integration. Follow integrations/costco/SETUP.md from the same GitHub tree as the runtime instructions. Do not buy anything. Do not scrape a token from the browser. Ask me for the DevTools secret the way that document says.
```

The bot will ask you to copy one value from Chrome on your own computer: Account → Orders & Purchases → Developer Tools → Application → Local Storage → the key whose name contains `refreshtoken` → only the `secret`. It uses that to read receipts and prices. It will show you the warehouses on the membership and wait until you confirm which one is home.

Setup is finished when the bot quotes the summary line the tool printed, including how far back the receipts go. The next “I’m going to Costco” question can use that history. A daily run stays quiet unless sign-in failed or a staple newly went on sale.

## For people changing the system

The household rules live in [AGENT_RUNTIME_INSTRUCTIONS.md](AGENT_RUNTIME_INSTRUCTIONS.md). The longer spec is [AGENT_BEHAVIOR_SPEC.md](AGENT_BEHAVIOR_SPEC.md). The sheet layout is [GOOGLE_SHEETS_SCHEMA.md](GOOGLE_SHEETS_SCHEMA.md). Costco’s procedure for the bot is [integrations/costco/SETUP.md](integrations/costco/SETUP.md).
