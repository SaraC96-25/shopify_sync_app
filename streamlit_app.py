import os
import io
import json
import time
from typing import Dict, List, Tuple

import pandas as pd
import requests
import streamlit as st

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
    "Content-Type": "application/json"
}

# -----------------------------
# 📄 Caricamento file
# -----------------------------
st.subheader("1) Carica l'Excel prodotti (foglio: Dati)")
uploaded_excel = st.file_uploader("Scegli il file Excel con foglio 'Dati'", type=["xlsx", "xls"]) 

# Per i prezzi accettiamo o un secondo foglio nello stesso Excel chiamato "Prezzi" oppure un file separato (CSV/Excel)
st.subheader("2) Carica il Listino Prezzi (Quantità×Posizione)")
price_file = st.file_uploader("Scegli il file prezzi (può essere il secondo foglio nello stesso Excel o un CSV separato)", type=["xlsx", "xls", "csv"], key="pricefile")

st.markdown("**Formato atteso per il foglio 'Dati':** colonne → `Titolo Prodotto`, `SKU`, `Posizione Stampa`, `Quantità` (il `Costo Fornitore` viene ignorato).\n\n**Formato atteso per il listino prezzi:** colonne → `Posizione Stampa`, `Quantità`, `Prezzo`. ")

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
        raise ValueError("Nel file prodotti deve essere presente un foglio 'Dati'.")
    df = xls.parse("Dati")
    df = normalize_columns(df)
    expected = {"titolo prodotto", "sku", "posizione stampa", "quantità"}
    missing = expected - set(df.columns)
    if missing:
        raise ValueError(f"Mancano colonne nel foglio Dati: {missing}")
    # cast
    df["quantità"] = pd.to_numeric(df["quantità"], errors="coerce").astype("Int64")
    # filtra quantità permesse
    df = df[df["quantità"].isin(ALLOWED_QT)].copy()
    # pulizia posizioni
    df["posizione stampa"] = df["posizione stampa"].str.strip()
    return df


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

def shopify_create_or_update_product(title: str, body_html: str, options: List[str], product_type: str = "") -> dict:
    """Crea il prodotto se non esiste, altrimenti aggiorna solo le opzioni.
    Strategia semplice: cerchiamo per title esatto. In contesti reali conviene usare un handle fisso o l'ID.
    """
    # Cerca prodotti con titolo
    q_params = {"title": title}
    r = requests.get(f"{BASE_URL}/products.json", headers=HEADERS, params=q_params, timeout=30)
    r.raise_for_status()
    items = r.json().get("products", [])

    payload = {
        "product": {
            "title": title,
            "body_html": body_html,
            "options": [{"name": o} for o in options]
        }
    }

    if items:
        prod_id = items[0]["id"]
        ur = requests.put(f"{BASE_URL}/products/{prod_id}.json", headers=HEADERS, data=json.dumps(payload), timeout=30)
        ur.raise_for_status()
        return ur.json()["product"]
    else:
        cr = requests.post(f"{BASE_URL}/products.json", headers=HEADERS, data=json.dumps(payload), timeout=30)
        cr.raise_for_status()
        return cr.json()["product"]


def shopify_replace_variants(product_id: int, variants: List[dict]) -> List[dict]:
    """Elimina varianti esistenti e inserisce le nuove in blocco."""
    # 1) Leggi varianti attuali
    r = requests.get(f"{BASE_URL}/products/{product_id}/variants.json", headers=HEADERS, timeout=30)
    r.raise_for_status()
    current = r.json().get("variants", [])
    # 2) Cancella varianti esistenti
    for v in current:
        vid = v["id"]
        dr = requests.delete(f"{BASE_URL}/variants/{vid}.json", headers=HEADERS, timeout=30)
        if dr.status_code not in (200, 201, 204):
            st.warning(f"Impossibile cancellare variante {vid}: {dr.status_code}")
        time.sleep(0.2)
    # 3) Crea nuove
    created = []
    for v in variants:
        cr = requests.post(f"{BASE_URL}/variants.json", headers=HEADERS, data=json.dumps({"variant": v}), timeout=30)
        cr.raise_for_status()
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
            "taxable": True
        })
    return variants

# -----------------------------
# 🚀 UI principale
# -----------------------------

products_df = None
prices_df = None
price_lookup = None

col1, col2 = st.columns(2)

with col1:
    if uploaded_excel:
        try:
            products_df = read_products_df(uploaded_excel)
            st.success("File prodotti letto correttamente.")
            st.dataframe(products_df.head(20))
        except Exception as e:
            st.error(f"Errore nel leggere l'Excel prodotti: {e}")

with col2:
    if price_file:
        try:
            prices_df = read_prices(price_file)
            st.success("Listino prezzi caricato.")
            st.dataframe(prices_df.head(20))
            price_lookup = build_price_lookup(prices_df)
        except Exception as e:
            st.error(f"Errore nel leggere il listino prezzi: {e}")

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
                # Pubblica sul canale Online Store (se serve). API moderne usano 'publication' e 'channels'; per semplicità lasciamo lo stato di default.
                pass

            st.success(f"Create {len(created_variants)} varianti per '{title}'.")
            created_summary.append({
                "Titolo": title,
                "SKU Base": sku,
                "# Varianti": len(created_variants)
            })
            time.sleep(0.4)

        if created_summary:
            st.subheader("✅ Riepilogo")
            st.dataframe(pd.DataFrame(created_summary))

st.divider()

st.markdown("""
### 📘 Note operative
- **Due opzioni**: l'app crea le opzioni **Quantità** e **Posizione Stampa** (non due varianti fisse). Le varianti generate sono solo le combinazioni presenti nel foglio **Dati** e con prezzo presente a listino.
- **Prezzi**: il prezzo viene preso dalla tabella `Posizione Stampa × Quantità`. Se una combinazione non ha prezzo, la variante viene **saltata** e segnalata.
- **SKU variante**: viene generato come `SKUBASE-<Qta>-<pos>`, max 63 caratteri.
- **Inventario**: impostato a 9999 per semplicità. Adatta a logica reale (magazzino/track inventory) se necessario.
- **Pubblicazione**: questo esempio non forza la pubblicazione su canali specifici; se il tema è attivo l'articolo risulta visibile una volta completato.
- **Deduplicazione prodotti**: la ricerca prodotto avviene per *titolo esatto*. In produzione conviene usare un `handle` o un ID salvato.

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
"""}
