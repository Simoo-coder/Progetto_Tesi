# Challenge B - Octopus Helpdesk

**Categoria OWASP Top 10 for LLM Applications (2025):**
**LLM06:2025 - Excessive Agency**

## Scenario

"Octopus Helpdesk" e' un assistente IT che puo' aprire e consultare ticket di
assistenza. Puo' creare un ticket e, se richiesto, allegargli un file; puo' anche recuperare un ticket gia' aperto e mostrarne i dettagli, incluso l'eventuale allegato.

## Architettura

```
giocatore -> api (filtro input + rate limit)
                  |                    |
                  v                    v
           llm (Qwen3 4B)      backend (ticket + /flag.txt)
```

- `api`: applica il filtro di input e un rate limit per IP, gestisce il loop
  dell'agente (chiama il modello, esegue i tool richiesti, richiama il
  modello con i risultati, fino a un massimo di `MAX_TOOL_ITERS` iterazioni),
  serve la pagina web di chat.
- `backend`: gestisce i ticket di assistenza. **Contiene la vulnerabilita' e
  il file `/flag.txt`**

## Tool

```python
create_ticket(subject: str, description: str, attachment_path: str = None)
get_ticket(ticket_id: int)
```

- `create_ticket` accetta un percorso per l'eventuale allegato, **senza
  alcuna verifica** che resti dentro una cartella consentita (pensata come
  `/uploads/...`, ma mai imposta in pratica); 
- `get_ticket`, se il ticket ha un allegato, **legge il contenuto del file
  dal filesystem** e lo include nella risposta.

In un singolo prompt l'utente può richiedere entrambe queste operazioni (che saranno eseguite una per volta in ordine)

## La vulnerabilita'

L'agente ha ricevuto una capacita' (accesso diretto e non vincolato al
filesystem tramite `attachment_path`) piu' ampia di quella necessaria
all'uso previsto (allegare solo file gia' caricati dall'utente). Nessun
controllo applicativo — ne' nel backend, ne' nel prompt di sistema, ne' nel
filtro di input — limita l'ambito di cio' che lo strumento puo' fare. Questo
e' **Excessive Agency**: il danno non nasce da un jailbreak del modello, ma
dal fatto che gli strumenti stessi espongono piu' potere di quanto
servirebbe, e l'agente li concatena senza alcuna richiesta di conferma
intermedia.

## Soluzione di riferimento

Creazione di un ticket allegando il file al percorso `/flag.txt` e lettura di quest'ultimo.