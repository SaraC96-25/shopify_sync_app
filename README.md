
# Shopify Variant Uploader (Streamlit)

Importa da Excel un listino strutturato **Quantità × Posizione Stampa** e crea/aggiorna **varianti Shopify** con opzioni:
- `Quantità`
- `Posizione Stampa`

Ignora il **Costo Fornitore**. Il prezzo è preso incrociando quantità e posizione.

## Struttura Excel attesa
- Colonne: `Titolo Prodotto`, `SKU`, `Costo Fornitore`, `Posizione Stampa`, `Quantità`, ... ulteriori colonne numeriche con quantità (2,3,4,5,10,15,20,50,100...).
- Per ogni prodotto:
  - **Riga 1 del blocco**: `Titolo Prodotto` e `SKU` valorizzati, `Posizione Stampa` vuota. Le colonne `Quantità` e successive contengono **breakpoint** (1,2,3, ... 100).
  - **Righe successive**: `Posizione Stampa` valorizzata (es. *Lato Cuore*, *Fronte*, ...). Le colonne quantità contengono i **prezzi**.

## Deploy (Streamlit Cloud)
1. Fai un repo GitHub con i file di questa cartella.
2. Su Streamlit Cloud, crea una nuova app puntando a `streamlit_app.py`.
3. In **Secrets**, imposta:
   ```toml
   SHOPIFY_SHOP_DOMAIN = "myshop.myshopify.com"
   SHOPIFY_ACCESS_TOKEN = "shpat_..."
   SHOPIFY_API_VERSION = "2024-10"
   ```
4. Avvia l'app. Carica l'Excel (upload) o inserisci l'URL `raw` del file su GitHub.

## Modalità operative
- **Dry-Run**: simula senza inviare chiamate a Shopify.
- **SKU varianti**: opzionale suffisso `-Q{qty}-P{pos}` per generare SKU univoci per variante.

## Note su Shopify
- L'app crea/aggiorna i prodotti per **Titolo Prodotto** (match esatto).
- Crea 2 opzioni (`Quantità`, `Posizione Stampa`) e tutte le combinazioni come varianti.
- Se una combinazione esiste, aggiorna **solo il prezzo**.

> Consiglio: mantenere i titoli prodotto in Excel allineati a quelli presenti in Shopify per aggiornamenti idempotenti.
