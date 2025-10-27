import os
import io
import json
import time
from typing import Dict, List, Tuple

import pandas as pd
import requests
import streamlit as st

# -----------------------------
# 🔢 Costanti globali
# -----------------------------
ALLOWED_QT = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 15, 20, 50, 100]
DEFAULT_POS = ["Lato Cuore", "Fronte", "Retro", "Lato Cuore + Retro", "Fronte + Retro"]

# -----------------------------
# ⚙️ Config & helpers
# -----------------------------
st.set_page_config(page_title="Shopify Variant Uploader", page_icon="🛍️", layout="wide")

st.title("🛍️ Import varianti Shopify da Excel")
st.caption("Crea automaticamente due opzioni (Quantità, Posizione Stampa) e tutte le varianti risultanti, con prezzi presi da una tabella Quantità×Posizione.")

# Legge i secrets da Streamlit Cloud → Settings → Secrets
SHOPIFY_STORE = st.secrets.get("SHOPIFY_STORE", "")  # es. mystore.myshopify.com
SHOPIFY_API_VERSION = st.secrets.get("SHOPIFY_API_VERSION", "2024-04")
SHOPIFY_ADMIN_TOKEN = st.secrets.get("SHOPIFY_ADMIN_TOKEN", "")

REQUIRED_SECRETS = ["SHOPIFY_STORE", "SHOPIFY_API_VERSION", "SHOPIFY_ADMIN_TOKEN"]

with st.expander("🔐 Stato credenziali Shopify", expanded=False):
    for key in REQUIRED_SECRETS:
        st.write(f"{key}: ", "✅" if st.secrets.get(key) else "❌")

if not (SHOPIFY_STORE and SHOPIFY_API_VERSION and SHOPIFY_ADMIN_TOKEN):
    st.warning("Configura i secrets (SHOPIFY_STORE, SHOPIFY_API_VERSION, SHOPIFY_ADMIN_TOKEN) nelle impostazioni di Streamlit Cloud per effettuare chiamate all'API Shopify.")

# Endpoint base
BASE_URL = f"https://{SHOPIFY_STORE}/admin/api/{SHOPIFY_API_VERSION}"
HEADERS = {
    "X-Shopify-Access-Token": SHOPIFY_ADMIN_TOKEN,
    "Content-Type": "application/json",
}

# -----------------------------
# 📄 Caricamento file
# -----------------------------
st.subheader("Carica il file Excel unico (Prodotti + Prezzi)")
uploaded_excel = st.file_uploader("Scegli l'Excel che contiene: foglio `Dati` e (opzionale) foglio `Prezzi`/`Listino`", type=["xlsx", "xls"]) 

st.markdown("**Foglio `Dati` (obbligatorio):** colonne → `Titolo Prodotto`, `SKU`, `Posizione Stampa`, `Quantità` (il `Costo Fornitore` viene ignorato).  ")
st.markdown("**Foglio `Prezzi`/`Listino` (opzionale nello stesso file):**
- formato **tidy**: colonne → `Posizione Stampa`, `Quantità`, `Prezzo`, **oppure**
- formato **matrice**: prima colonna = `Posizione Stampa`, colonne successive = quantità (1,2,3,...), celle = prezzo.").

**Formato atteso per il listino prezzi:** colonne → `Posizione Stampa`, `Quantità`, `Prezzo`.")

ALLOWED_QT = [1,2,3,4,5,6,7,8,9,10,15,20,50,100]
DEFAULT_POS = ["Lato Cuore","Fronte","Retro","Lato Cuore + Retro","Fronte + Retro"]

# -----------------------------
# 🧠 Funzioni dati
# -----------------------------

def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    mapper = {c: c.strip().lower() for c in df.columns}
    df = df.rename(columns=mapper)
    return df


def read_products_df(file) -> pd.DataFrame:
    xls = pd.ExcelFile(file)
    if "Dati" not in xls.sheet_names:
        raise ValueError("Nel file deve essere presente un foglio 'Dati'.")
    df = xls.parse("Dati")
    df = normalize_columns(df)
    expected = {"titolo prodotto", "sku", "posizione stampa", "quantità"}
    missing = expected - set(df.columns)
    if missing:
        raise ValueError(f"Mancano colonne nel foglio Dati: {missing}")
    df["quantità"] = pd.to_numeric(df["quantità"], errors="coerce").astype("Int64")
    df = df[df["quantità"].isin(ALLOWED_QT)].copy()
    df["posizione stampa"] = df["posizione stampa"].str.strip()
    return df


def read_prices_from_excel(file) -> pd.DataFrame:
    """Cerca un foglio prezzi nello *stesso* Excel.
    Supporta due formati:
      1) tidy: colonne [Posizione Stampa, Quantità, Prezzo]
      2) matrice: prima colonna Posizione Stampa, colonne successive = quantità
    Se non trova un foglio prezzi, ritorna None e useremo eventuale colonna 'prezzo' nel Dati.
    """
    xls = pd.ExcelFile(file)
    candidate_names = ["Prezzi", "Listino", "prezzi", "listino", "Prices", "prices"]
    sheet = None
    for name in candidate_names:
        if name in xls.sheet_names:
            sheet = name
            break
    if sheet is None:
        # Prova a trovare un foglio che sembri un listino: contiene una colonna Posizione Stampa e varie colonne numeriche
        for s in xls.sheet_names:
            if s == "Dati":
                continue
            tmp = normalize_columns(xls.parse(s))
            cols = set(tmp.columns)
            if "posizione stampa" in cols:
                # se ci sono colonne numeriche corrispondenti ad ALLOWED_QT => matrice
                qty_like = [c for c in tmp.columns if c.isdigit() and int(c) in ALLOWED_QT]
                tidy_like = {"posizione stampa", "quantità", "prezzo"}.issubset(cols)
                if qty_like or tidy_like:
                    sheet = s
                    break
    if sheet is None:
        return None

    dfp = normalize_columns(xls.parse(sheet))

    # Caso tidy
    if {"posizione stampa", "quantità", "prezzo"}.issubset(set(dfp.columns)):
        dfp["quantità"] = pd.to_numeric(dfp["quantità"], errors="coerce").astype("Int64")
        dfp["prezzo"] = pd.to_numeric(dfp["prezzo"], errors="coerce")
        dfp = dfp.dropna(subset=["quantità","prezzo"]).copy()
        dfp["posizione stampa"] = dfp["posizione stampa"].str.strip()
        return dfp

    # Caso matrice: melt
    if "posizione stampa" in dfp.columns:
        qty_cols = [c for c in dfp.columns if c != "posizione stampa" and (str(c).isdigit() and int(c) in ALLOWED_QT)]
        if qty_cols:
            melted = dfp.melt(id_vars=["posizione stampa"], value_vars=qty_cols, var_name="quantità", value_name="prezzo")
            melted["quantità"] = pd.to_numeric(melted["quantità"], errors="coerce").astype("Int64")
            melted["prezzo"] = pd.to_numeric(melted["prezzo"], errors="coerce")
            melted = melted.dropna(subset=["prezzo"]).copy()
            melted["posizione stampa"] = melted["posizione stampa"].str.strip()
            return melted

    return None


def read_prices(file_or_excel) -> pd.DataFrame:
    if file_or_excel is None:
        raise ValueError("Carica un listino prezzi.")
    name = getattr(file_or_excel, 'name', '')
    if name.endswith(".csv"):
        dfp = pd.read_csv(file_or_excel)
    else:
        xls = pd.ExcelFile(file_or_excel)
        # se esiste foglio Prezzi, usa quello; altrimenti prova il primo
        sheet = "Prezzi" if "Prezzi" in xls.sheet_names else xls.sheet_names[0]
        dfp = xls.parse(sheet)
    dfp = normalize_columns(dfp)
    expected = {"posizione stampa", "quantità", "prezzo"}
    missing = expected - set(dfp.columns)
    if missing:
        raise ValueError(f"Nel listino prezzi mancano colonne: {missing}")
    dfp["quantità"] = pd.to_numeric(dfp["quantità"], errors="coerce").astype("Int64")
    dfp["prezzo"] = pd.to_numeric(dfp["prezzo"], errors="coerce")
    dfp = dfp.dropna(subset=["quantità","prezzo"]).copy()
    dfp["posizione stampa"] = dfp["posizione stampa"].str.strip()
    return dfp


def build_price_lookup(prices_df: pd.DataFrame) -> Dict[Tuple[str, int], float]:
    lookup = {}
    for _, r in prices_df.iterrows():
        key = (str(r["posizione stampa"]).strip(), int(r["quantità"]))
        lookup[key] = float(r["prezzo"])  # prezzo finale Shopify
    return lookup


# -----------------------------
# 🧱 Shopify REST helpers
# -----------------------------

def _shopify_request(method: str, path: str, **kwargs) -> requests.Response:
    url = f"{BASE_URL}{path}"
    try:
        r = requests.request(method, url, headers=HEADERS, timeout=30, **kwargs)
        if r.status_code >= 400:
            # Mostra corpo e codice errore in Streamlit per debug
            try:
                body = r.json()
            except Exception:
                body = r.text
            st.error(f"Errore Shopify {r.status_code} su {path}:
{body}")
        r.raise_for_status()
        return r
    except requests.RequestException as e:
        # Prova a esporre il body anche in caso di eccezione
        resp = getattr(e, 'response', None)
        body = None
        if resp is not None:
            try:
                body = resp.json()
            except Exception:
                body = resp.text
        st.error(f"Richiesta Shopify fallita: {e}
Dettagli: {body}")
        raise


def shopify_find_product_by_sku_or_title(sku: str, title: str) -> dict | None:
    """Cerca prima per SKU variante (affidabile), poi per titolo (client-side)."""
    # 1) Cerca variante per SKU
    try:
        r = _shopify_request("GET", f"/variants.json", params={"sku": sku})
        variants = r.json().get("variants", [])
        if variants:
            prod_id = variants[0]["product_id"]
            pr = _shopify_request("GET", f"/products/{prod_id}.json")
            return pr.json().get("product")
    except Exception:
        pass

    # 2) Cerca prodotti e filtra per titolo esatto client-side (pagine fino a 250)
    try:
        r = _shopify_request("GET", f"/products.json", params={"limit": 250, "status": "any", "fields": "id,title,handle"})
        products = r.json().get("products", [])
        for p in products:
            if str(p.get("title", "")).strip().lower() == title.strip().lower():
                pr = _shopify_request("GET", f"/products/{p['id']}.json")
                return pr.json().get("product")
    except Exception:
        pass

    return None


def shopify_create_or_update_product(title: str, body_html: str, options: List[str], product_type: str = "", base_sku: str | None = None) -> dict:
    existing = shopify_find_product_by_sku_or_title(base_sku or "", title)

    payload = {
        "product": {
            "title": title,
            "body_html": body_html,
            "product_type": product_type,
            "options": [{"name": o} for o in options],
        }
    }

    if existing:
        prod_id = existing["id"]
        ur = _shopify_request("PUT", f"/products/{prod_id}.json", data=json.dumps(payload))
        return ur.json()["product"]
    else:
        cr = _shopify_request("POST", f"/products.json", data=json.dumps(payload))
        return cr.json()["product"]


def shopify_replace_variants(product_id: int, variants: List[dict]) -> List[dict]:
    """Sostituisce tutte le varianti del prodotto in modo sicuro.
    - Cancella le varianti esistenti
    - Crea le nuove **usando l'endpoint corretto** per il prodotto:
      POST /products/{product_id}/variants.json
    """
    # 1) Leggi varianti attuali
    r = _shopify_request("GET", f"/products/{product_id}/variants.json")
    current = r.json().get("variants", [])

    # 2) Cancella varianti esistenti
    for v in current:
        vid = v["id"]
        try:
            _shopify_request("DELETE", f"/variants/{vid}.json")
        except Exception:
            st.warning(f"Impossibile cancellare variante {vid}")
        time.sleep(0.2)

    # 3) Crea nuove (endpoint specifico del prodotto)
    created = []
    for v in variants:
        v_with_pid = {**v, "product_id": product_id}
        cr = _shopify_request("POST", f"/products/{product_id}/variants.json", data=json.dumps({"variant": v_with_pid}))
        created.append(cr.json()["variant"])
        time.sleep(0.2)
    return created

# -----------------------------
# 🔧 Costruzione varianti
# -----------------------------

def make_variant_sku(base_sku: str, qty: int, pos: str) -> str:
    pos_slug = (
        pos.lower()
        .replace(" ", "-")
        .replace("+", "plus")
        .replace("à", "a").replace("è","e").replace("é","e").replace("ì","i").replace("ò","o").replace("ù","u")
    )
    return f"{base_sku}-{qty}-{pos_slug}"[:63]


def build_variants_for_product(df_prod: pd.DataFrame, price_lookup: Dict[Tuple[str,int], float], default_currency: str = "EUR") -> List[dict]:
    variants = []
    for _, row in df_prod.iterrows():
        qty = int(row["quantità"])
        pos = str(row["posizione stampa"]).strip()
        key = (pos, qty)
        price = price_lookup.get(key)
        if price is None:
            st.warning(f"Prezzo mancante per combinazione: {pos} × {qty}. Variante saltata.")
            continue
        sku = make_variant_sku(row["sku"], qty, pos)
        variants.append({
            "option1": qty,                  # Quantità
            "option2": pos,                  # Posizione Stampa
            "price": f"{price:.2f}",
            "sku": sku,
            "inventory_management": "shopify",
            "inventory_quantity": 9999,
            "taxable": True,
        })
    return variants

# -----------------------------
# 🚀 UI principale
# -----------------------------

products_df = None
prices_df = None
price_lookup = None

if uploaded_excel:
    try:
        products_df = read_products_df(uploaded_excel)
        st.success("File 'Dati' letto correttamente.")
        st.dataframe(products_df.head(20))

        # 1) prova a trovare un foglio prezzi nello stesso file
        prices_df = read_prices_from_excel(uploaded_excel)

        # 2) se non trovato, prova a vedere se in Dati c'è già una colonna prezzo
        if prices_df is None and "prezzo" in products_df.columns:
            tmp = products_df[["posizione stampa", "quantità", "prezzo"]].copy()
            prices_df = tmp.dropna(subset=["prezzo"]).copy()

        if prices_df is not None:
            st.success("Listino prezzi rilevato nello stesso file.")
            st.dataframe(prices_df.head(20))
            price_lookup = build_price_lookup(prices_df)
        else:
            st.warning("Non ho trovato un foglio prezzi. Aggiungi un foglio 'Prezzi' o 'Listino' nello stesso file, oppure inserisci la colonna 'Prezzo' nel foglio Dati.")

    except Exception as e:
        st.error(f"Errore nel leggere l'Excel: {e}")

st.divider()

st.subheader("3) Impostazioni di pubblicazione")
default_body = "<p>Prodotto caricato automaticamente via Streamlit.</p>"
product_type = st.text_input("Product type (opzionale)", value="Personalizzato")
publish = st.toggle("Pubblica il prodotto (published)", value=True)

st.info("Per ogni 'Titolo Prodotto' verranno create 2 opzioni: Quantità e Posizione Stampa. Le varianti saranno tutte le combinazioni presenti nel foglio 'Dati'.")

if st.button("🔁 Crea/aggiorna prodotti su Shopify", type="primary"):
    if products_df is None or price_lookup is None:
        st.error("Carica sia il file prodotti (Dati) che il listino prezzi prima di procedere.")
    elif not (SHOPIFY_STORE and SHOPIFY_ADMIN_TOKEN):
        st.error("Credenziali Shopify non configurate.")
    else:
        # ciclo per prodotto
        created_summary = []
        for (title, sku), df_grp in products_df.groupby(["titolo prodotto", "sku"], dropna=False):
            st.write("")
            st.write(f"▶️ **{title}** — SKU base: `{sku}`")

            product = shopify_create_or_update_product(
                title=title,
                body_html=default_body,
                options=["Quantità", "Posizione Stampa"],
                product_type=product_type,
            )
            prod_id = product["id"]
            st.write(f"ID prodotto Shopify: {prod_id}")

            variants = build_variants_for_product(df_grp, price_lookup)
            if not variants:
                st.warning("Nessuna variante costruita (forse mancano prezzi).")
                continue

            created_variants = shopify_replace_variants(prod_id, variants)

            # pubblicazione
            if publish:
                # Pubblica sul canale Online Store (se necessario). API moderne usano 'publication'/'channels'; qui lasciamo lo stato di default.
                pass

            st.success(f"Create {len(created_variants)} varianti per '{title}'.")
            created_summary.append({
                "Titolo": title,
                "SKU Base": sku,
                "# Varianti": len(created_variants),
            })
            time.sleep(0.4)

        if created_summary:
            st.subheader("✅ Riepilogo")
            st.dataframe(pd.DataFrame(created_summary))

st.divider()

st.markdown(
    """
### 📘 Note operative
- **Due opzioni**: l'app crea le opzioni **Quantità** e **Posizione Stampa** (non due varianti fisse). Le varianti generate sono solo le combinazioni presenti nel foglio **Dati** e con prezzo presente a listino.
- **Prezzi**: il prezzo viene preso dalla tabella `Posizione Stampa × Quantità`. Se una combinazione non ha prezzo, la variante viene **saltata** e segnalata.
- **SKU variante**: viene generato come `SKUBASE-<Qta>-<pos>`, max 63 caratteri.
- **Inventario**: impostato a 9999 per semplicità. Adatta la logica reale se necessario.
- **Pubblicazione**: questo esempio non forza la pubblicazione su canali specifici.
- **Deduplicazione prodotti**: la ricerca prodotto avviene per *titolo esatto*. In produzione conviene usare un `handle` o ID.

### 🔑 Secrets da impostare su Streamlit Cloud
```toml
# .streamlit/secrets.toml
SHOPIFY_STORE = "mystore.myshopify.com"
SHOPIFY_API_VERSION = "2024-04"
SHOPIFY_ADMIN_TOKEN = "shpat_..."
```

### 🧪 Struttura file prezzi (esempio)
```
Posizione Stampa,Quantità,Prezzo
Fronte,1,12.90
Fronte,2,20.00
Retro,1,12.90
Lato Cuore,1,10.90
Fronte + Retro,1,18.90
...
```

### 🚩 Limiti & miglioramenti futuri
- Ricerca per SKU (via InventoryItem) per associare a prodotti già esistenti.
- Gestione immagini per varianti.
- Canali di pubblicazione / status prodotto.
- Sincronizzazione parziale: aggiungere solo varianti mancanti invece di sostituirle.
    """
)
