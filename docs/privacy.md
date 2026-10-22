---
title: Privacy Policy
---

# Privacy Policy for the job-application-agent Gmail integration

Last updated: 21 October 2026

job-application-agent is an open-source, self-hosted tool. Each person runs their own copy on their own computer or server, under their own Google Cloud project. There is no central service, no hosted account and no third party that receives data.

## What the tool accesses

When you connect a Gmail account, the tool requests the `gmail.modify` scope. It uses that access to:

- read job alert emails and replies from employers and recruiters in the connected mailbox,
- send application emails and follow-ups that you configured,
- apply and remove labels so processed messages can be told apart from new ones.

The tool does not request permanent deletion of messages and does not change account settings.

## Where the data goes

Everything stays on the machine you run the tool on. Email contents are read into memory to find job offers and replies, and short notes about them (sender, subject, date, your decision) are written to local tracking files you control. The OAuth token is stored in a local file in your home directory. Nothing is uploaded to the author of this software or to any analytics service.

If you configure optional services (for example a language model API to draft text, or a search API to find job listings), the tool sends them only the text you configured it to send. Those services are governed by their own terms and you connect them with your own keys.

## Google API Services User Data Policy

Use of information received from Google APIs follows the Google API Services User Data Policy, including the Limited Use requirements. The tool uses Gmail data only to provide the features described above, does not transfer it to third parties except as needed to provide those features, and does not use it for advertising.

## Revoking access

You can remove the tool's access at any time at https://myaccount.google.com/permissions. Deleting the local token file has the same effect for the tool.

## Contact

Questions about this policy: open an issue at https://github.com/vmoskviak/job-application-agent/issues.
