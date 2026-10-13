# Registro dei giri (RSI)

Un giro = una modifica + `python bench/run.py` + confronto. Token = byte/4, stima.

| Giro | Modifica | Avvio ms | Token fissi | engines ms | pick freddo ms | Esito |
|---|---|---|---|---|---|---|
| 0 | base (e755b21) | 654 | 1472 | 368 | 165 | riferimento |
| 1 | via motore claude, Codex legge solo la coda | 642 | 1472 | 19.22 | 10 | tenuto |
| 2 | descrizioni corte + schemi senza titoli e senza ramo null | 663 | 673 | ~19 | 9 | tenuto |
| 3 | server MCP senza libreria mcp (fastserver) | 28 | 673 | vedi json | 12 | tenuto |
