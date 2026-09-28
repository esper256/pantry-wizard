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

### 2. Let Grok Bot into that Google account

Grok Bot does not take a pasted OAuth token. You sign Google in through its plugins, with an account that can **edit** your copy.

1. In Grok Bot, open **Plugins** on the sidebar. On the phone, tap your avatar, then **Plugins**.
2. Add **Google Drive** and **Google Sheets**.
3. When it says **Authorize** or **Authenticate**, finish the Google login in the browser. If it says **Waiting for authorization**, choose **Reopen**.
4. Confirm both plugins are under **Installed**, and that neither says **Needs auth** or **Disconnected**.

Drive is how the bot finds the file. Sheets is how it edits cells. A sheet shared as **Viewer** stays view-only. The plugin cannot change sharing for you.

Use the Google account that owns the copy. If Grok Bot is already connected to a different Google account, share your copy with that account as **Editor** (Share → add the address → Editor). Open the sheet once in that account and confirm you can type in a cell.

Plugin connections belong to the Grok Bot account you signed in with. Every bot on that account can use them.

### 3. If Google sign-in fails

| What you see | What to do |
|---|---|
| **Needs auth** or **Disconnected** | Open the plugin and choose **Authorize** again. Installed does not mean signed in. |
| **Waiting for authorization** | Choose **Reopen** so the browser tab comes back, then finish Google’s page. |
| The connect card never appears | Re-add the plugin and finish the provider login. Steps: [Connect plugins](https://cursor.com/help/grok-bot/connect-plugins). |
| **Disabled by team admin** | A Cursor team admin has to enable the plugin. That is separate from Google. |
| Google says an admin must review **Grok** | A company Google account can block the sign-in. Grok Bot signs in to Google as Grok, not as Cursor. Ask the Google admin to trust the app named Grok (Security → Access and data control → API controls → App access control). A personal Gmail account does not need that. |
| **Access blocked** | Trusting Grok in the Google Admin console is the fix. Switching browsers does not clear an admin block. |
| The bot cannot find the sheet | Drive is missing, or the connected Google account cannot open your copy. Share the copy as Editor with that account, or reconnect Drive and Sheets as the account that owns it. |
| The bot can read but not edit | The connected account is a Viewer. Change the share to Editor. |

If authorize completes and the plugin still says disconnected, write to hi@cursor.com with the plugin name and the email on your Grok Bot account.

Do not paste a Google token, a Costco password, or a Costco refresh token into the chat.

### 4. The prompt

Start a new Grok Bot chat and send this. Replace the sheet URL with your copy.

```text
Set up Pantry Wizard for this household.

Our workbook is this Google Sheet, our copy, not the template:
<paste the URL of your copy>

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

### 5. How you know it is ready

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
