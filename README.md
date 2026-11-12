# Pantry Wizard

An agent-first shopping list that combines real understanding powered by Grok Bot, or ChatGPT Dots with the durability and reliability imbued by the Google Sheet that backs it.

Say what happened in the kitchen, in plain language, to your Bot and immediately put your phone back in your pocket. Other household members can do the same. The bots coordinate behind the scenes using the shared Google Sheet, forgetting nothing.

> “We’re out of milk.”
>
> “I opened a tube of toothpaste.”
>
> “We usually buy coffee at Safeway.”
>
> “Here’s a photo of my grocery receipt.”

The sheet keeps track of what’s been said. Using up a tube of toothpaste doesn’t mean you are out, but it does mean you might check the cupboard before your next shopping trip.

> “I’m heading to Safeway. What do I need to know?”

The answer comes from the sheet: what was asked for, what is probably low, and what you already have, filtered by an intelligent bot that understands your habits.

Costco is an optional integration. The bot can read one warehouse’s receipts and today’s prices, including Costco’s promotions.

> “I’m going to Costco, what’s on sale that I should stock up on?”

Pantry Wizard is an instruction manual for bots to become the ultimate shopping list.

## Set up your own copy

You need a Google account and a subscription that includes access to a Bot (either Grok Bot, or ChatGPT Dots). The bot must be able to run Python and keep a secret.

### 1. Make a blank sheet

Create a new Google Sheet and name it something like “Pantry Wizard”. Leave it empty. The bot adds the tabs.

Copy its URL from the address bar. That is the URL you will give the bot.

### 2. The prompt

Start a new chat with the bot and send this. Replace the sheet URL with yours.

The bot walks you through Google Cloud. You do the clicks it names. When it asks for the key, attach the JSON file to the chat, or hand it over in the secure field it shows. The message itself stays free of keys.

```text
Set up Pantry Wizard for this household.

Our workbook is this Google Sheet:
<paste the URL of your sheet>

Walk me through Google Cloud so you can read and write that sheet with the Sheets API.
Have me select or create a project named Pantry Wizard, enable the Google Sheets API, create a service account, and download a JSON key.
When the service-account form asks for a project role, tell me to leave the role blank and continue. A Cloud IAM role does not open the spreadsheet. Skip the Drive API as well.
After you have the key, tell me the client_email and have me share this sheet with that address as Editor. That share is the permission.
Take the JSON key as a file I attach, or through a secure field. Store it as a bot secret. Do not ask me to paste the private key into the chat, and do not print the key.

Then load these instructions and follow them from now on, in every chat about shopping:
https://raw.githubusercontent.com/esper256/pantry-wizard/v2.0.0/AGENT_INSTRUCTIONS.md

Do the setup section in them. Use the pw tool for everything. Do not use the Google Drive or Google Sheets plugins, and do not write your own code against the sheet.
Run pw doctor. If it says the sheet is not set up, ask me for our timezone and currency and run pw init with them. If the sheet is already set up, another person's bot did that. Leave it as it is.
Ask me what to call me. That is the name you record my requests under.
Do not invent items, quantities, or prices.

When pw doctor reports no problems, tell me, and tell me what pw list shows.
```

### 3. What you will click

The bot sends the links and waits. A few screens in that console are easy to misread.

Select the Pantry Wizard project in the top bar, then enable the Google Sheets API. The bot will point at `https://console.cloud.google.com/apis/library/sheets.googleapis.com`. If the project link misses, open that page and pick Pantry Wizard from the project picker.

Then: **IAM & Admin → Service Accounts → Create service account**. The form’s second step offers a role on the Cloud project. Leave that role empty and continue. The same for the optional step that grants other people access to the service account. That dropdown is project IAM. It does not give anyone the spreadsheet, and picking Owner or Editor there only adds Cloud permissions you do not need.

On the new service account: **Keys → Add key → Create new key → JSON**. Google downloads one file. Attach that file to the chat, or drop it in the secure field. See [Store secrets securely](https://cursor.com/help/grok-bot/secrets). The bot keeps the key as a secret for later chats.

Share your sheet with the service account as **Editor**. The address ends in `.iam.gserviceaccount.com`. The bot reads `client_email` from the JSON and tells you which address to add. That share is what lets it read and write the sheet.

The Sheets API is the one to enable. If a step offers the Drive API, skip it.

### 4. If the Sheets API refuses the bot

| What you see | What to do |
|---|---|
| `accessNotConfigured`, or the API has not been used | Enable the Google Sheets API on the Pantry Wizard project, wait a minute, and try again. |
| The form asks you to pick a role | Leave the role blank and continue. |
| `403` The caller does not have permission | Share your sheet with the service account email as Editor. A project IAM role will not fix this. |
| The bot cannot open the sheet, and the address it used is your Gmail | Share the sheet with the `client_email` from the JSON, the address ending in `.iam.gserviceaccount.com`. |
| The bot asks you to paste the JSON or the private key | Attach the file, or use the secure field. If the key already appeared in the chat, delete that key under the service account’s Keys list and create a new JSON key. |
| The key is rejected, or `invalid_grant` | The key was deleted, or it belongs to another project. Create a new JSON key on this service account and attach that file. |

### 5. How you know it is ready

The bot is ready when it tells you `pw doctor` found no problems and that the list is empty. In the sheet you will see new tabs: `Config`, `Sources`, `List`, `Items`, `Log` and the rest.

Try one ordinary sentence: “We’re out of milk.” The bot should answer in a sentence, and a row should appear in the `Log` tab. If it cannot open the sheet, it should say so and leave the sheet alone.

It is not ready if it answers the trip question from chat memory, adds items you never mentioned, or writes its own script to edit the sheet.

You do not need to clone this Git repository yourself. The bot installs the tool and loads the instructions from the links above.

### 6. A second person

Each adult uses their own bot. Send the same prompt with the same sheet URL. The second bot needs its own service-account key, or the same JSON file, and the sheet shared with that key’s address. It will find the sheet already set up.

## Add Costco

Once the bot is ready, it can connect one Costco warehouse. In the same chat:

```text
Install the Costco integration. Follow integrations/costco/SETUP.md from the same release as your instructions. Do not buy anything. Do not scrape a token from the browser. Ask me for the DevTools secret the way that document says.
```

The bot will ask you to copy one value from Chrome on your own computer: Account → Orders & Purchases → Developer Tools → Application → Local Storage → the key whose name contains `refreshtoken` → only the `secret`. It uses that to read receipts and prices. It will show you the warehouses on the membership and wait until you confirm which one is home.

Setup is finished when the bot quotes the summary line the tool printed, including how far back the receipts go. The next “I’m going to Costco” question can use that history. A daily run stays quiet unless sign-in failed or a staple newly went on sale.

Only one bot runs the Costco import. The other bot reads what it wrote.

## Coming from the old sheet

A household that started before version 2.0 has a sheet with `Items`, `RetailMemory`, `Integrations` and `Events` tabs. [docs/MIGRATION.md](docs/MIGRATION.md) moves it into a new sheet and leaves the old one untouched. The old instructions ([AGENT_RUNTIME_INSTRUCTIONS.md](AGENT_RUNTIME_INSTRUCTIONS.md), [AGENT_BEHAVIOR_SPEC.md](AGENT_BEHAVIOR_SPEC.md), [GOOGLE_SHEETS_SCHEMA.md](GOOGLE_SHEETS_SCHEMA.md)) stay in this repository until those households have moved, because their sheets point at them. New households do not use them.

## For people changing the system

| What | Where |
|---|---|
| What an agent loads | [AGENT_INSTRUCTIONS.md](AGENT_INSTRUCTIONS.md) |
| The `pw` tool both agents use for every read and write | [pw/](pw/README.md) |
| Sheet layout | [docs/SCHEMA.md](docs/SCHEMA.md), with made-up rows in [docs/SHEET_TEMPLATE.md](docs/SHEET_TEMPLATE.md) |
| Store integrations | [integrations/README.md](integrations/README.md) |
| Costco: the procedure the bot follows, and the importer | [integrations/costco/SETUP.md](integrations/costco/SETUP.md), [integrations/costco/README.md](integrations/costco/README.md) |

Run the tests with `cd pw && PYTHONPATH=src:tests python -m unittest discover -s tests` and `cd integrations/costco && pip install -e ".[dev]" && pytest`.
