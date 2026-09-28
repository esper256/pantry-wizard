# Pantry Wizard

An agent first shopping list that combines real understanding powered by Grok Bot with the durability and reliability imbued by the Google Sheet that backs it.

Say what happened in the kitchen. In plain language to the Grok Bot and bot records it in the Google Sheet. Other household members can do the same, and their bots coordinate behind the scenes using the shared Google Sheet.

> “We’re out of milk.”
>
> “I opened a tube of toothpaste.”
>
> “We usually buy coffee at Safeway.”
>
> “Here's a photo of my grocery receipt.”

The sheet keeps track of what's been said. Using up a tube of toothpaste doesn't mean you are out, but it does mean you might check the cupboard before a shopping trip.

> “I’m heading to Safeway. What do I need to know?”

The answer comes from the sheet: what was asked for, what is probably low, and what you already have, filtered by an intelligent Bot that understands your habits.

Costco is an optional integration. The bot can read one warehouse’s receipts and today’s prices including Costco’s promotions.

> ”I'm going to Costco, what's on sale that I should stock up on?”

Pantry Wizard is an instruction manual for bots to become the ultimate shopping list.

## Set up your own copy

You need a Google account and [Grok Bot](https://cursor.com/download), signed in.

### 1. Copy the template sheet

Open the template:

https://docs.google.com/spreadsheets/d/1_y9qlX15wQshsmXMEK9uGuXCzr7tS58lNc1y1mdkARs/edit?usp=sharing

Choose **File → Make a copy**. Name it something like “Pantry Wizard”. The copy is yours. Leave the template alone.

Copy the URL of **your** sheet from the address bar. That is the URL you will give the bot. If you paste the template link, the bot will try to edit a sheet it should not change.

### 2. The prompt

Start a new chat with that bot and send this. Replace the sheet URL with your copy.

The bot walks you through Google Cloud. You do the clicks it names. When it asks for the key, attach the JSON file to the chat, or hand it over in the secure field it shows. The message itself stays free of keys.

```text
Set up Pantry Wizard for this household.

Our workbook is this Google Sheet, our copy, not the template:
<paste the URL of your copy>

Walk me through Google Cloud so you can read and write that sheet with the Sheets API.
Have me select or create a project named Pantry Wizard, enable the Google Sheets API, create a service account, and download a JSON key.
When the service-account form asks for a project role, tell me to leave the role blank and continue. A Cloud IAM role does not open the spreadsheet. Skip the Drive API as well.
After you have the key, tell me the client_email and have me share this sheet with that address as Editor. That share is the permission.
Take the JSON key as a file I attach, or through a secure field. Store it as a bot secret. Do not ask me to paste the private key into the chat, and do not print the key.
Call https://sheets.googleapis.com for the spreadsheet id in the URL above. Do not use the Google Drive or Google Sheets plugins.

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

### 3. What you will click

The bot sends the links and waits. A few screens in that console are easy to misread.

Select the Pantry Wizard project in the top bar, then enable the Google Sheets API. The bot will point at `https://console.cloud.google.com/apis/library/sheets.googleapis.com`. If the project link misses, open that page and pick Pantry Wizard from the project picker.

Then: **IAM & Admin → Service Accounts → Create service account**. The form’s second step offers a role on the Cloud project. Leave that role empty and continue. The same for the optional step that grants other people access to the service account. That dropdown is project IAM. It does not give anyone the spreadsheet, and picking Owner or Editor there only adds Cloud permissions you do not need.

On the new service account: **Keys → Add key → Create new key → JSON**. Google downloads one file. Attach that file to the chat, or drop it in the secure field. See [Store secrets securely](https://cursor.com/help/grok-bot/secrets). The bot keeps the key as a secret for later chats.

Share your sheet copy with the service account as **Editor**. The address ends in `.iam.gserviceaccount.com`. The bot reads `client_email` from the JSON and tells you which address to add. That share is what lets it read and write the sheet.

The Sheets API is the one to enable. If a step offers the Drive API, skip it.

### 4. If the Sheets API refuses the bot

| What you see | What to do |
|---|---|
| `accessNotConfigured`, or the API has not been used | Enable the Google Sheets API on the Pantry Wizard project, wait a minute, and try again. |
| The form asks you to pick a role | Leave the role blank and continue. |
| `403` The caller does not have permission | Share your copy with the service account email as Editor. A project IAM role will not fix this. |
| The bot cannot open the sheet, and the address it used is your Gmail | Share the sheet with the `client_email` from the JSON, the address ending in `.iam.gserviceaccount.com`. |
| The bot asks you to paste the JSON or the private key | Attach the file, or use the secure field. If the key already appeared in the chat, delete that key under the service account’s Keys list and create a new JSON key. |
| The key is rejected, or `invalid_grant` | The key was deleted, or it belongs to another project. Create a new JSON key on this service account and attach that file. |

### 5. How you know it is ready

The bot is ready when it says this exact line:

> Shopping data steward active — runtime instructions v0.8 loaded.

It should also quote `protocol_version` from your sheet’s Config tab. After this setup that value should be `0.8`. If the sheet was older, the bot updates it without asking. That is expected.

Try one ordinary sentence: “We’re out of milk.” The bot should write that to the sheet, and you should be able to see the change. If it cannot open the sheet, it should say so and leave the sheet alone.

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
