from __future__ import annotations

import html
import os
import sqlite3
from pathlib import Path
from typing import Any
from urllib.parse import quote

import asyncio
import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

app = FastAPI(title="TETR Analytics", version="1.0.0")
DATA_FILE = Path(os.getenv("TETR_DATA_FILE", "tetr_analytics.sqlite3"))


class Standard(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    category: str = Field(min_length=2, max_length=80)
    minimum_rating: int = Field(ge=0)
    description: str = Field(min_length=2, max_length=500)


class PredictionRequest(BaseModel):
    player_a: str = Field(min_length=1, max_length=32)
    player_b: str = Field(min_length=1, max_length=32)


def db() -> sqlite3.Connection:
    connection = sqlite3.connect(DATA_FILE)
    connection.row_factory = sqlite3.Row
    connection.execute("CREATE TABLE IF NOT EXISTS standards (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, category TEXT NOT NULL, minimum_rating INTEGER NOT NULL, description TEXT NOT NULL)")
    connection.commit()
    return connection


def tetr_profile(username: str) -> dict[str, Any]:
    response = httpx.get(f"https://ch.tetr.io/api/users/{quote(username, safe='')}", headers={"Accept": "application/json"}, timeout=10)
    payload = response.json()
    if response.status_code >= 400 or not payload.get("success"):
        raise HTTPException(404, f"TETR.IO user '{username}' was not found")
    user = payload["data"]["user"]
    league = user.get("league") or {}
    return {"username": user.get("username", username), "country": user.get("country"), "league": ({"rank": league.get("rank"), "rating": league.get("rating", 0), "percentile": league.get("percentile", 0)} if league else None)}


@app.get("/api/tetrio/profile")
def profile(username: str):
    return tetr_profile(username.strip())


@app.post("/api/tetrio/compare")
def compare(request: PredictionRequest):
    a, b = tetr_profile(request.player_a.strip()), tetr_profile(request.player_b.strip())
    ar = (a.get("league") or {}).get("rating", 0)
    br = (b.get("league") or {}).get("rating", 0)
    return {"players": [a, b], "rating_delta": abs(ar - br), "favorite": a["username"] if ar >= br else b["username"]}


@app.post("/api/tetrio/predict")
def predict(request: PredictionRequest):
    result = compare(request)
    delta = result["rating_delta"]
    probability = round(1 / (1 + 10 ** (-delta / 400)) * 100, 1)
    return {**result, "favorite_probability": probability, "confidence": "high" if delta >= 300 else "medium" if delta >= 100 else "low", "method": "Elo-style rating comparison"}


@app.get("/api/standards")
def standards():
    connection = db()
    rows = [dict(row) for row in connection.execute("SELECT * FROM standards ORDER BY minimum_rating DESC, id DESC")]
    connection.close()
    return rows


@app.post("/api/standards")
def add_standard(standard: Standard):
    connection = db()
    cursor = connection.execute("INSERT INTO standards (name, category, minimum_rating, description) VALUES (?, ?, ?, ?)", tuple(standard.model_dump().values()))
    connection.commit()
    item = {"id": cursor.lastrowid, **standard.model_dump()}
    connection.close()
    return JSONResponse(item, status_code=201)


@app.post("/api/webhooks/discord")
async def discord_webhook(request: Request):
    payload = await request.json()
    content = str(payload.get("content", "")).strip()
    if content.startswith("/tetr profile "):
        return {"type": "message", "content": str(tetr_profile(content.removeprefix("/tetr profile ").strip()))}
    return {"type": "message", "content": "TETR Analytics commands: /tetr profile <username>, /tetr predict <playerA> <playerB>, /tetr standards"}


PAGE = """<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>TETR Analytics</title><style>
:root{color-scheme:dark;font-family:Inter,ui-sans-serif,system-ui,sans-serif;background:#071015;color:#e5edf0}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 15% 0,#12313a,transparent 35%),#071015}main{max-width:1180px;margin:auto;padding:38px 22px}header{display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid #1a343d;padding-bottom:28px}h1{margin:0;color:#7be8f4;letter-spacing:.08em;font-size:20px}h2{font-size:30px;margin:48px 0 10px}.muted{color:#8ca0a6}.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-top:24px}.card{border:1px solid #1b3b44;background:#0b1a20;border-radius:12px;padding:20px}.card h3{margin-top:0}.search{display:flex;gap:10px;margin-top:24px}input{flex:1;background:#08151a;color:#e5edf0;border:1px solid #31515a;border-radius:8px;padding:12px}button{background:#54dce8;color:#061015;border:0;border-radius:8px;padding:12px 18px;font-weight:700;cursor:pointer}.result{margin-top:16px;border-left:3px solid #54dce8;padding:12px;background:#0d252c;white-space:pre-wrap}@media(max-width:760px){.grid{grid-template-columns:1fr}header{display:block}.search{flex-direction:column}}
</style></head><body><main><header><h1>TETR ANALYTICS</h1><span class='muted'>FastAPI single-file edition</span></header><h2>Competitive intelligence for TETR.IO</h2><p class='muted'>Live profiles, rating comparisons, predictions, and reviewed standards in one Python service.</p><section class='card'><h3>Player profile</h3><div class='search'><input id='username' placeholder='TETR.IO username'><button onclick='profile()'>Search</button></div><div id='profile' class='result' hidden></div></section><div class='grid'><article class='card'><h3>Standards registry</h3><p class='muted'>Store and review rating standards through the API.</p><code>GET /api/standards</code></article><article class='card'><h3>Match predictor</h3><p class='muted'>Elo-style prediction from live ratings.</p><code>POST /api/tetrio/predict</code></article><article class='card'><h3>Discord bot</h3><p class='muted'>Webhook-compatible command endpoint.</p><code>POST /api/webhooks/discord</code></article></div></main><script>async function profile(){const name=document.querySelector('#username').value.trim();if(!name)return;const box=document.querySelector('#profile');box.hidden=false;box.textContent='Loading...';const r=await fetch('/api/tetrio/profile?username='+encodeURIComponent(name));const data=await r.json();box.textContent=r.ok?JSON.stringify(data,null,2):data.detail}</script></body></html>"""


@app.get("/", response_class=HTMLResponse)
def dashboard():
    return HTMLResponse(PAGE)


async def run_discord_bot() -> None:
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        print("[v0] DISCORD_BOT_TOKEN is not set; Discord gateway bot is disabled")
        return

    import discord
    from discord import app_commands

    intents = discord.Intents.default()
    bot = discord.Client(intents=intents)
    tree = app_commands.CommandTree(bot)

    @bot.event
    async def on_ready():
        await tree.sync()
        print(f"[v0] Discord bot online as {bot.user}")

    tetr = app_commands.Group(name="tetr", description="TETR Analytics commands")

    @tetr.command(name="profile", description="Look up a TETR.IO player")
    async def profile_command(interaction: discord.Interaction, username: str):
        await interaction.response.defer()
        try:
            player = await asyncio.to_thread(tetr_profile, username.strip())
            league = player.get("league")
            summary = "No Tetra League rating available."
            if league:
                summary = f"{league['rank']} · {league['rating']:,} TR · {league['percentile']:.2f} percentile"
            await interaction.followup.send(f"**{player['username']}** ({player.get('country') or 'N/A'})\\n{summary}")
        except HTTPException as error:
            await interaction.followup.send(str(error.detail), ephemeral=True)

    @tetr.command(name="predict", description="Predict a match between two players")
    async def predict_command(interaction: discord.Interaction, player_a: str, player_b: str):
        await interaction.response.defer()
        try:
            result = await asyncio.to_thread(predict, PredictionRequest(player_a=player_a, player_b=player_b))
            await interaction.followup.send(f"Favorite: **{result['favorite']}** ({result['favorite_probability']}%) · confidence: {result['confidence']}")
        except HTTPException as error:
            await interaction.followup.send(str(error.detail), ephemeral=True)

    @tetr.command(name="standards", description="List reviewed rating standards")
    async def standards_command(interaction: discord.Interaction):
        items = standards()
        message = "\\n".join(f"• {item['name']}: {item['minimum_rating']} TR — {item['description']}" for item in items) or "No reviewed standards yet."
        await interaction.response.send_message(message[:2000])

    tree.add_command(tetr)
    await bot.start(token)


@app.on_event("startup")
async def start_background_services():
    print("[BOT DEBUG] Startup event triggered", flush=True)

    token = os.getenv("DISCORD_BOT_TOKEN")
    print(f"[BOT DEBUG] Token configured: {bool(token)}", flush=True)

    if token:
        app.state.discord_task = asyncio.create_task(run_discord_bot())
        print("[BOT DEBUG] Bot task created", flush=True)
    else:
        print("[BOT DEBUG] DISCORD_BOT_TOKEN is missing", flush=True)



@app.on_event("shutdown")
async def stop_background_services():
    task = getattr(app.state, "discord_task", None)
    if task:
        task.cancel()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
