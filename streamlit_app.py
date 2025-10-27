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
    "Content-Type": "application/json",
}

# -----------------------------
# 📄 Caricamento file
# -----------------------------
st.subheader("Carica il file Excel unico (Prodotti + Prezzi)")
uploaded_excel = st.file_uploader(
    "Scegli l'Excel che contiene: foglio `Dati` e (opzionale) foglio `Prezzi` o `Listino`",
    type=["xlsx", "xls"]
)

st.markdown(
    "**Foglio `Dati` (obbligatorio):** colonne → `Titolo Prodotto`, `SKU`, `Posizione Stampa`, `Quantità` (il `Costo Fornitore` viene ignorato).  "
)
st.markdown(
    "**Foglio `Prezzi` o `Listino` (opzionale nello stesso file):**\n"
    "- formato **tidy**: colonne → `Posizione Stampa`, `Quantità`, `Prezzo`, **oppure**\n"
    "- formato **matrice**: prima colonna = `Posizione Stampa`, colonne successive = quantità (1,2,3,...), celle = prezzo."
)

ALLOWED_QT = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 15, 20, 50, 100]
DEFAULT_POS = ["Lato Cuore", "Fronte", "Retro", "Lato Cuore + Retro", "Fronte + Retro"]

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
    """Cerca un foglio prezzi nello stesso Excel.
    Supporta:
      1) formato tidy: colonne [Posizione Stampa, Quantità, Prezzo]
      2) formato matrice: prima colonna Posizione Stampa, colonne successive = quantità
    """
    xls = pd.ExcelFile(file)
    candidate_names = ["Prezzi", "Listino", "prezzi", "listino", "Prices", "prices"]
    sheet = None
    for name in candidate_names:
        if name in xls.sheet_names:
            sheet = name
            break
    if sheet is None:
        for s in xls.sheet_names:
            if s == "Dati":
                continue
            tmp = normalize_columns(xls.parse(s))
            cols = set(tmp.columns)
            if "posizione stampa" in cols:
                qty_like = [c for c in tmp.columns if c.isdigit() and int(c) in ALLOWED_QT]
                tidy_like = {"posizione stampa", "quantità", "prezzo"}.issubset(cols)
                if qty_like or tidy_like:
                    sheet = s
                    break
    if sheet is None:
        return None

    dfp = normalize_columns(xls.parse(sheet))

    if {"posizione stampa", "quantità", "prezzo"}.issubset(set(dfp.columns)):
        dfp["quantità"] = pd.to_numeric(dfp["quantità"], errors="coerce").astype("Int64")
        dfp["prezzo"] = pd.to_numeric(dfp["prezzo"], errors="coerce")
        dfp = dfp.dropna(subset=["quantità", "prezzo"]).copy()
        dfp["posizione stampa"] = dfp["posizione stampa"].str.strip()
        return dfp

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


def build_price_lookup(prices_df: pd.DataFrame) -> Dict[Tuple[str, int], float]:
    lookup = {}
    for _, r in prices_df.iterrows():
        key = (str(r["posizione stampa"]).strip(), int(r["quantità"]))
        lookup[key] = float(r["prezzo"])
    return lookup


# -----------------------------
# 🧱 Shopify REST helpers
# -----------------------------
def shopify_create_or_update_product(title: str, body_html: str, options: List[str], product_type: str = "") -> dict:
    q_params = {"title": title}
    r = requests.get(f"{BASE_URL}/products.json", headers=HEADERS, params=q_params, timeout=30)
    r.raise_for_status()
    items = r.json().get("products", [])

    payload = {
        "product": {
            "title": title,
            "body_html": body_html,
            "product_type": product_type,
            "options": [{"name": o} for o in options],
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
    r = requests.get(f"{BASE_URL}/products/{product_id}/variants.json", headers=HEADERS, timeout=30)
    r.raise_for_status()
    current = r.json().get("variants", [])
    for v in current:
        vid = v["id"]
        dr = requests.delete(f"{BASE_URL}/variants/{vid}.json", headers=HEADERS, timeout=30)
        time.sleep(0.2)
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
        .replace("à", "a")
        .replace("è", "e")
        .replace("é", "e")
        .replace("ì", "i")
        .replace("ò", "o")
        .replace("ù", "u")
    )
    return f"{base_sku}-{qty}-{pos_slug}"[:63]


def build_variants_for_product(df_prod: pd.DataFrame, price_lookup: Dict[Tuple[str, int], float]) -> List[dict]:
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
        variants.append(
            {
                "option1": qty,
                "option2": pos,
                "price": f"{price:.2f}",
                "sku": sku,
                "inventory_management": "shopify",
                "inventory_quantity": 9999,
                "taxable": True,
            }
        )
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

        prices_df = read_prices_from_excel(uploaded_excel)
        if prices_df is None and "prezzo" in products_df.columns:
            tmp = products_df[["posizione stampa", "quantità", "prezzo"]].copy()
            prices_df = tmp.dropna(subset=["prezzo"]).copy()

        if prices_df is not None:
            st.success("Listino prezzi rilevato nello stesso file.")
            st.dataframe(prices_df.head(20))
            price_lookup = build_price_lookup(prices_df)
        else:
            st.warning("Non ho trovato un foglio prezzi. Aggiungi un foglio 'Prezzi' o inserisci la colonna 'Prezzo' nel foglio Dati.")

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
        st.error("Carica il file Excel con Dati e Prezzi prima di procedere.")
    elif not (SHOPIFY_STORE and SHOPIFY_ADMIN_TOKEN):
        st.error("Credenziali Shopify non configurate.")
    else:
        created_summary = []
        for (title, sku), df_grp in products_df.groupby(["titolo prodotto", "sku"], dropna=False):
            st.write(f"▶️ **{title}** — SKU base: `{sku}`")

            product = shopify_create_or_update_product(
                title=title,
                body_html=default_body,
                options=["Quantità", "Posizione Stampa"],
                product_type=product_type,
            )
            prod_id = product["id"]
            variants = build_variants_for_product(df_grp, price_lookup)
            if not variants:
                st.warning("Nessuna variante costruita (forse mancano prezzi).")
                continue

            created_variants = shopify_replace_variants(prod_id, variants)
            if publish:
                pass

            st.success(f"Create {len(created_variants)} varianti per '{title}'.")
            created_summary.append({"Titolo": title, "SKU Base": sku, "# Varianti": len(created_variants)})
            time.sleep(0.4)

        if created_summary:
            st.subheader("✅ Riepilogo")
            st.dataframe(pd.DataFrame(created_summary))

st.divider()

st.markdown(
    """
### 📘 Note operative
- **Un solo file Excel**: foglio `Dati` obbligatorio, foglio `Prezzi`/`Listino` facoltativo.
- **Prezzi**: se manca il foglio prezzi, ma il foglio `Dati` ha la colonna `Prezzo`, verrà usata quella.
- **SKU variante**: generato come `SKUBASE-<Qta>-<pos>`.
- **Inventario**: impostato a 9999 per semplicità.
- **Deduplicazione**: ricerca prodotto per titolo esatto.

### 🔑 Secrets da impostare
```toml
SHOPIFY_STORE = "mystore.myshopify.com"
SHOPIFY_API_VERSION = "2024-04"
SHOPIFY_ADMIN_TOKEN = "shpat_..."
