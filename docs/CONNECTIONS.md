# Connecting accounts and APIs

Open **Connections** in Hagent's main navigation. Use **Add connection** to choose
Account connector, API, or MCP, then configure and manage it on the same page.
Connections belong to the current
workspace and only selected agents receive their tools. The same tools work with
API model runtimes and coding CLI runtimes; they do not depend on a ChatGPT or
Claude desktop connector subscription.

## Gmail and Google Drive

Choose **Sign in to Gmail** or **Sign in to Google Drive**. Hagent creates the
connection and redirects to Google for account selection and consent. Users do
not enter a client ID or client secret. Select agents and save their access.
Choose **Test connection** to verify a
   real account request independently of any model.

The installation owner enables Google sign-in once in **Google sign-in settings**:

1. Create a Google Cloud project and enable **Gmail API** and **Google Drive API**.
2. Configure Google's OAuth consent screen. During Testing, add your own account
   as a test user. Public distribution may require Google's app verification.
3. Create a **Web application** OAuth client and register the exact callback URL
   displayed in Google sign-in settings. For the current local server this is
   `http://127.0.0.1:8003/connections/google/callback`.
4. Save the client ID and secret once in those settings. Alternatively, configure
   `HAGENT_GOOGLE_CLIENT_ID` and `HAGENT_GOOGLE_CLIENT_SECRET` on the Hagent server.
   This app configuration is reused for subsequent Gmail and Drive sign-ins.

Gmail exposes search/read message tools. Drive exposes file search, metadata,
Google Docs text export, and text/JSON file reading. Other binary files return
metadata. These connectors request only `gmail.readonly` and `drive.readonly`;
they do not send emails, edit files, delete data, or change sharing permissions.
Search supports page tokens and bounded result counts. File responses are capped
at 2 MiB. Offline refresh tokens let Hagent renew access without repeated sign-in.

**Disconnect** revokes the Google grant and removes agent access. Google may
revoke all tokens for the same account/client grant: if another Google connection
stops working after disconnecting, sign in to that connection again.

OAuth uses an expiring, single-use state bound to an HTTP-only browser cookie and
PKCE. Google client secrets and tokens are encrypted at rest with Windows DPAPI
for the current Windows user. On other systems, Fernet uses a private local key
beside the Hagent configuration, or `HAGENT_CONNECTION_KEY`. Preserve that key
when moving the database. Credentials are injected server-side and never included
in the model's tool schemas. Do not place them in URLs, task prompts, or comments.

## Direct APIs

Configure a fixed JSON endpoint and no auth, bearer-token auth, or an API-key
header. An agent can supply query parameters but cannot change the endpoint or
perform write requests. Remote endpoints require HTTPS; HTTP is allowed only
for explicit localhost services. Authorization is not forwarded through redirects.

## MCP

Register a local stdio command, remote SSE endpoint, or Streamable HTTP endpoint.
Environment credentials and authorization headers entered through Connections
are encrypted. Existing CLI-created stdio/SSE registrations remain compatible.
MCP server tools may include write operations: assign only appropriate servers to
an agent. Direct model-free calls are also available to local workflow code via
`hagent.mcp_client.call_tool_sync(connection, tool_name, arguments)`.

References: [Google OAuth](https://developers.google.com/identity/protocols/oauth2/web-server),
[Gmail reads](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/get),
[Drive downloads/exports](https://developers.google.com/workspace/drive/api/guides/manage-downloads).
