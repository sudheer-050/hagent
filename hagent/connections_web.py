"""Connection setup UI and Google OAuth authorization-code flow."""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select, update

from hagent import connections as service
from hagent.connection_secrets import protect, reveal
from hagent.db import get_active_workspace, get_session
from hagent.models import Agent, AppSetting, McpServer


def google_app(session):
    client_id = os.environ.get("HAGENT_GOOGLE_CLIENT_ID", "")
    client_secret = os.environ.get("HAGENT_GOOGLE_CLIENT_SECRET", "")
    if client_id and client_secret:
        return {"client_id": client_id, "client_secret": client_secret, "managed": True}
    workspace = get_active_workspace(session)
    row = session.get(AppSetting, "google_oauth_app_" + workspace.id)
    return reveal(row.value) if row else {}


def install(app, templates, require):
    def same_origin(request: Request):
        origin = request.headers.get("origin")
        if request.method == "POST" and origin:
            from urllib.parse import urlsplit
            if urlsplit(origin).netloc != request.url.netloc:
                raise HTTPException(403, "Cross-origin connection changes are not allowed")

    router = APIRouter(dependencies=[Depends(same_origin)])

    def notice(message):
        return RedirectResponse("/connections?" + urlencode({"notice": message}), status_code=303)

    @router.get("/connections")
    def connections_page(request: Request):
        with get_session() as session:
            cards = []
            for item in session.scalars(select(McpServer).order_by(McpServer.name)).all():
                cfg = service.config(item)
                cards.append({"id": item.id, "name": item.name, "label": cfg.get("label", item.name),
                              "transport": item.transport, "service": cfg.get("service", ""),
                              "connected": cfg.get("connected", False), "last_test": cfg.get("last_test", ""),
                              "agent_ids": [agent.id for agent in item.agents]})
            agents = session.scalars(select(Agent).where(Agent.archived.is_(False)).order_by(Agent.name)).all()
            return templates.TemplateResponse(request, "connections.html", {
                "connections": cards, "agents": agents, "active": "connections",
                "notice": request.query_params.get("notice", ""),
                "redirect_uri": str(request.url_for("google_connection_callback")),
                "google_ready": bool(google_app(session).get("client_id")),
            })

    @router.get("/connections/google/settings")
    def google_settings(request: Request):
        with get_session() as session:
            settings = google_app(session)
            return templates.TemplateResponse(request, "google_connection_settings.html", {
                "active": "connections", "client_id": settings.get("client_id", ""),
                "configured": bool(settings.get("client_secret")), "managed": settings.get("managed", False),
                "redirect_uri": str(request.url_for("google_connection_callback")),
                "notice": request.query_params.get("notice", ""),
            })

    @router.post("/connections/google/settings")
    def save_google_settings(client_id: str = Form(...), client_secret: str = Form("")):
        with get_session() as session:
            existing = google_app(session)
            if existing.get("managed"):
                return notice("Google sign-in is managed by this installation's environment settings")
            secret = client_secret.strip() or existing.get("client_secret", "")
            if not client_id.strip().endswith(".apps.googleusercontent.com") or not secret:
                return RedirectResponse("/connections/google/settings?" + urlencode({
                    "notice": "Enter a Google web application client ID and secret to enable sign-in.",
                }), status_code=303)
            workspace = get_active_workspace(session)
            key = "google_oauth_app_" + workspace.id
            row = session.get(AppSetting, key)
            if row is None:
                row = AppSetting(key=key, value="")
                session.add(row)
            row.value = protect({"client_id": client_id.strip(), "client_secret": secret})
            session.commit()
        return notice("Google sign-in enabled. Choose Gmail or Google Drive and sign in.")

    @router.post("/connections/google/connect")
    def connect_google(request: Request, service_name: str = Form(...)):
        if service_name not in service.GOOGLE_SCOPES:
            raise HTTPException(400, "Choose Gmail or Google Drive")
        with get_session() as session:
            settings = google_app(session)
            if not settings.get("client_id") or not settings.get("client_secret"):
                return notice("Google sign-in needs to be enabled once for this Hagent installation. Open Google sign-in settings.")
            workspace = get_active_workspace(session)
            pending = next((item for item in session.scalars(select(McpServer).where(McpServer.transport == "google")).all()
                            if service.config(item).get("service") == service_name
                            and not service.config(item).get("connected")
                            and service.config(item).get("client_id") == settings["client_id"]), None)
            if pending is None:
                label = "Gmail" if service_name == "gmail" else "Google Drive"
                pending = McpServer(workspace_id=workspace.id, name=service_name + "_" + secrets.token_hex(3),
                                    transport="google", config_json=json.dumps({
                                        "label": label, "service": service_name, "client_id": settings["client_id"],
                                        "connected": False, "credentials": protect({"client_secret": settings["client_secret"]}),
                                    }))
                session.add(pending)
            else:
                cfg = service.config(pending)
                cfg["credentials"] = protect({"client_secret": settings["client_secret"]})
                pending.config_json = json.dumps(cfg)
            session.commit()
            connection_id = pending.id
        return google_start(request, connection_id)

    @router.post("/connections")
    def create_connection(label: str = Form(...), kind: str = Form(...),
                          service_name: str = Form("gmail"), client_id: str = Form(""), client_secret: str = Form(""),
                          url: str = Form(""), auth: str = Form("none"), token: str = Form(""), key_name: str = Form("X-API-Key"),
                          command: str = Form(""), arguments: str = Form("[]"), environment: str = Form("{}"), headers: str = Form("{}")):
        try:
            label = label.strip()
            if not label or len(label) > 80:
                raise ValueError("Enter a connection name of 1–80 characters")
            if kind not in {"google", "api", "stdio", "sse", "http"}:
                raise ValueError("Choose Google, direct API, or an MCP transport")
            cfg = {"label": label}
            private = {}
            if kind == "google":
                if service_name not in service.GOOGLE_SCOPES:
                    raise ValueError("Choose Gmail or Google Drive")
                if not client_id.strip().endswith(".apps.googleusercontent.com") or not client_secret.strip():
                    raise ValueError("Enter the Google web application client ID and client secret")
                cfg.update(service=service_name, client_id=client_id.strip(), connected=False)
                private["client_secret"] = client_secret.strip()
            elif kind == "api":
                service.validate_url(url)
                if auth not in {"none", "bearer", "header"} or (auth != "none" and not token):
                    raise ValueError("Choose an authentication type and enter its API key or token")
                if not re.fullmatch(r"[A-Za-z0-9-]{1,80}", key_name):
                    raise ValueError("Use a valid API key header name")
                cfg.update(auth=auth, key_name=key_name)
                private["token"] = token
            else:
                args = json.loads(arguments)
                env = json.loads(environment)
                extra_headers = json.loads(headers)
                if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
                    raise ValueError("MCP arguments must be a JSON array of strings")
                for values in (env, extra_headers):
                    if not isinstance(values, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in values.items()):
                        raise ValueError("Environment and headers must be JSON objects with string values")
                if kind == "stdio" and not command.strip():
                    raise ValueError("Enter the installed MCP server command")
                if kind != "stdio":
                    service.validate_url(url)
                private.update(env=env, headers=extra_headers)
            cfg["credentials"] = protect(private)
            with get_session() as session:
                workspace = get_active_workspace(session)
                tool_name = re.sub(r"[^a-z0-9_]", "_", label.lower())[:32] or "connection"
                item = McpServer(workspace_id=workspace.id, name=tool_name + "_" + secrets.token_hex(3),
                                 transport=kind, url=url.strip() or None, command=command.strip() or None,
                                 args_json=arguments if kind in {"stdio", "sse", "http"} else "[]", config_json=json.dumps(cfg))
                session.add(item)
                session.commit()
            return notice("Connection saved. Assign agents below, then sign in or test it.")
        except (ValueError, TypeError):
            # JSON parser errors can contain submitted secrets: never echo their text.
            return notice("Connection not saved. Check the required fields, URL, authentication, and JSON values.")

    @router.post("/connections/{connection_id}/agents")
    def assign_agents(connection_id: str, agent_ids: list[str] = Form(default=[])):
        with get_session() as session:
            item = require(session, McpServer, connection_id)
            item.agents = [require(session, Agent, agent_id) for agent_id in set(agent_ids)]
            session.commit()
        return notice("Agent access updated")

    @router.post("/connections/{connection_id}/test")
    def test(connection_id: str):
        with get_session() as session:
            item = require(session, McpServer, connection_id)
            try:
                result = service.test_connection(item)
            except Exception:
                result = "Connection test failed. Check sign-in, service permissions, and network access."
            cfg = service.config(item)
            cfg["last_test"] = result
            item.config_json = json.dumps(cfg)
            session.commit()
        return notice(result)

    @router.post("/connections/{connection_id}/disconnect")
    def disconnect(connection_id: str):
        with get_session() as session:
            item = require(session, McpServer, connection_id)
            cfg = service.config(item)
            if item.transport == "google":
                values = service.credentials(item)
                token = values.get("refresh_token") or values.get("access_token")
                if token:
                    try:
                        # Google's revoke response is empty; do not parse it as JSON.
                        service.request("POST", "https://oauth2.googleapis.com/revoke", data={"token": token}, text=True)
                    except Exception:
                        return notice("Google could not revoke access. Retry disconnect, or revoke Hagent from your Google account first.")
            item.agents = []
            session.delete(item)
            session.commit()
        return notice("Disconnected and removed agent access")

    @router.post("/connections/{connection_id}/google/start")
    def google_start(request: Request, connection_id: str):
        with get_session() as session:
            item = require(session, McpServer, connection_id)
            if item.transport != "google":
                raise HTTPException(400, "Not a Google connection")
            cfg = service.config(item)
            state = item.id + "." + secrets.token_urlsafe(32)
            verifier = secrets.token_urlsafe(64)
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
            redirect_uri = str(request.url_for("google_connection_callback"))
            service.validate_url(redirect_uri)
            cfg["oauth"] = {"state_hash": hashlib.sha256(state.encode()).hexdigest(), "expires": time.time() + 600,
                            "verifier": protect({"value": verifier}), "redirect_uri": redirect_uri}
            item.config_json = json.dumps(cfg)
            session.commit()
            params = {"client_id": cfg["client_id"], "redirect_uri": redirect_uri, "response_type": "code",
                      "scope": service.GOOGLE_SCOPES[cfg["service"]], "access_type": "offline", "prompt": "consent",
                      "state": state, "code_challenge": challenge, "code_challenge_method": "S256"}
            response = RedirectResponse("https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params), status_code=303)
            response.set_cookie("hagent_google_" + item.id, state, httponly=True, samesite="lax",
                                secure=request.url.scheme == "https", max_age=600, path="/connections/google/callback")
            return response

    @router.get("/connections/google/callback", name="google_connection_callback")
    def google_callback(request: Request, state: str = "", code: str = "", error: str = ""):
        connection_id = state.split(".", 1)[0]
        with get_session() as session:
            item = require(session, McpServer, connection_id)
            cfg = service.config(item)
            flow = cfg.get("oauth", {})
            cookie = request.cookies.get("hagent_google_" + item.id, "")
            if (item.transport != "google" or not cookie or not hmac.compare_digest(cookie, state)
                    or not hmac.compare_digest(flow.get("state_hash", ""), hashlib.sha256(state.encode()).hexdigest())
                    or flow.get("expires", 0) <= time.time()):
                raise HTTPException(400, "Sign-in expired or state did not match; start again from Connections")
            original_config = item.config_json
            cfg.pop("oauth", None)
            consumed = session.execute(update(McpServer).where(
                McpServer.id == item.id, McpServer.config_json == original_config,
            ).values(config_json=json.dumps(cfg)), execution_options={"synchronize_session": False}).rowcount
            session.commit()  # Claim the state atomically, including when consent is refused.
            if consumed != 1:
                raise HTTPException(400, "Sign-in was already used; start again from Connections")
            session.refresh(item)
            response = notice("Google sign-in was cancelled. No account access was added.")
            if not error and code:
                try:
                    values = service.credentials(item)
                    tokens = service.request("POST", service.TOKEN_URL, data={
                        "grant_type": "authorization_code", "code": code, "client_id": cfg["client_id"],
                        "client_secret": values["client_secret"], "redirect_uri": flow["redirect_uri"],
                        "code_verifier": reveal(flow["verifier"])["value"],
                    })
                    if not tokens.get("access_token") or not tokens.get("refresh_token"):
                        raise ValueError("Offline access not granted; reconnect")
                    granted = set(tokens.get("scope", "").split())
                    if granted and service.GOOGLE_SCOPES[cfg["service"]] not in granted:
                        raise ValueError("Required service scope not granted")
                    values.update(access_token=tokens["access_token"], refresh_token=tokens["refresh_token"],
                                  expires_at=time.time() + tokens.get("expires_in", 3600))
                    cfg.update(connected=True, credentials=protect(values))
                    item.config_json = json.dumps(cfg)
                    session.commit()
                    response = notice("Google account connected. Assign agents to allow them to use it.")
                except Exception:
                    response = notice("Google sign-in could not finish. Check the client credentials, enabled API, and exact redirect URI, then sign in again.")
            response.delete_cookie("hagent_google_" + item.id, path="/connections/google/callback")
            response.headers["Cache-Control"] = "no-store"
            response.headers["Referrer-Policy"] = "no-referrer"
            return response

    app.include_router(router)
