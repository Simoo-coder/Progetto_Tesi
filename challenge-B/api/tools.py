import httpx

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "create_ticket",
            "description": "Apre un ticket IT.",
            "parameters": {
                "type": "object",
                "properties": {
                    "subject": {"type": "string", "description": "Oggetto breve."},
                    "description": {"type": "string", "description": "Descrizione del problema."},
                    "attachment_path": {
                        "type": "string",
                        "description": "Percorso di un file da allegare (opzionale).",
                    },
                },
                "required": ["subject", "description"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_ticket",
            "description": "Recupera un ticket esistente, incluso l'allegato.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ticket_id": {"type": "integer", "description": "Numero del ticket."},
                },
                "required": ["ticket_id"],
            },
        },
    },
]


async def dispatch_tool(name: str, args: dict, backend_url: str, client: httpx.AsyncClient) -> dict:
    try:
        if name == "create_ticket":
            resp = await client.post(
                f"{backend_url}/tickets",
                json={
                    "subject": args.get("subject", ""),
                    "description": args.get("description", ""),
                    "attachment_path": args.get("attachment_path"),
                },
            )
            resp.raise_for_status()
            return resp.json()

        if name == "get_ticket":
            ticket_id = args.get("ticket_id")
            resp = await client.get(f"{backend_url}/tickets/{ticket_id}")
            resp.raise_for_status()
            return resp.json()

        return {"error": f"Strumento sconosciuto: {name}"}
    except httpx.HTTPError as e:
        return {"error": f"Errore nel contattare il backend: {e}"}
