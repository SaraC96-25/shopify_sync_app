
import os
import io
import pandas as pd
import streamlit as st
from parser import parse_pricing_matrix
from shopify_client import ShopifyClient

st.set_page_config(page_title="Shopify Variant Uploader", page_icon="🧵", layout="wide")

st.title("🧵 Import Varianti Shopify da Excel (Quantità × Posizione Stampa)")

st.markdown("""
Questa app importa da Excel un listino strutturato come **Quantità × Posizione Stampa** e lo trasforma in varianti Shopify.
- Opzioni prodotto create: **Quantità** e **Posizione Stampa**
- Ignora il **Costo Fornitore**
- Per ogni riga prodotto (Titolo/SKU) crea/aggiorna tutte le combinazioni possibili con il **prezzo** corretto.
""")

with st.sidebar:
    st.header("Configurazione Shopify")
    shop_domain = st.text_input("Shop Domain (es. myshop.myshopify.com)", value=os.getenv("SHOPIFY_SHOP_DOMAIN",""))
    access_token = st.text_input("Admin API Access Token", type="password", value=os.getenv("SHOPIFY_ACCESS_TOKEN",""))
    api_version = st.text_input("API Version", value=os.getenv("SHOPIFY_API_VERSION","2024-10"))
    dry_run = st.checkbox("Dry-Run (non inviare a Shopify)", value=True)
    sku_suffix_mode = st.selectbox("SKU Varianti", ["<SKU>-Q{qty}-P{pos}", "Nessuno (usa SKU base)"])

st.header("1) Carica Excel")
src = st.radio("Origine dati", ["Upload diretto","URL GitHub (raw)"], horizontal=True)

df_raw = None
if src == "Upload diretto":
    file = st.file_uploader("Trascina il file Excel", type=["xlsx","xls"])
    if file is not None:
        df_raw = pd.read_excel(file, sheet_name=0)
else:
    url = st.text_input("URL RAW GitHub all'Excel")
    if url:
        df_raw = pd.read_excel(url, sheet_name=0)

if df_raw is not None:
    st.success(f"File caricato. Righe: {len(df_raw)} | Colonne: {len(df_raw.columns)}")
    with st.expander("Anteprima dati grezzi"):
        st.dataframe(df_raw.head(30), use_container_width=True)

    st.header("2) Parsing → Tidy")
    try:
        tidy = parse_pricing_matrix(df_raw)
        st.dataframe(tidy.head(50), use_container_width=True)
        st.info(f"Totale righe (combinazioni): {len(tidy)} · Prodotti unici: {tidy['Titolo Prodotto'].nunique()}")
    except Exception as e:
        st.error(f"Errore nel parsing: {e}")
        st.stop()

    st.header("3) Spinta verso Shopify")
    st.caption("Vengono create le opzioni di prodotto: Quantità, Posizione Stampa. Le varianti hanno prezzo dalla tabella.")
    run = st.button("Esegui creazione/aggiornamento")

    if run:
        if dry_run:
            st.warning("Dry-Run attivo: non verrà inviato nulla a Shopify. Vedi log sotto.")
        try:
            client = None if dry_run else ShopifyClient(shop_domain, access_token, api_version)
        except Exception as e:
            st.error(str(e))
            st.stop()

        logs = []
        for (title, sku), grp in tidy.groupby(["Titolo Prodotto","SKU"]):
            logs.append(f"— Prodotto: {title} (SKU base: {sku})")
            # Crea/Aggiorna il prodotto con 2 opzioni
            if not dry_run:
                product = client.create_or_update_product(
                    title=title,
                    options=[{"name":"Quantità"}, {"name":"Posizione Stampa"}],
                )
                product_id = product["id"]
                existing_variants = client.list_variants(product_id)
                # dizionario per match su (qty,pos)
                existing_map = {(v.get("option1"), v.get("option2")): v for v in existing_variants}
            else:
                product_id = None
                existing_map = {}

            # Per ogni combinazione qty×pos crea/aggiorna
            for (pos, qty), row in grp.set_index(["Posizione Stampa","Quantità"]).iterrows():
                price = row["Prezzo"]
                opt1 = str(qty)
                opt2 = str(pos)
                if sku_suffix_mode == "<SKU>-Q{qty}-P{pos}" and isinstance(sku, str):
                    variant_sku = f"{sku}-Q{qty}-P{str(pos).replace(' ', '')}"
                else:
                    variant_sku = sku if isinstance(sku, str) else None

                if (opt1, opt2) in existing_map:
                    v = existing_map[(opt1, opt2)]
                    vid = v["id"]
                    old_price = v.get("price")
                    action = f"UPDATE prezzo {old_price} → {price}"
                    logs.append(f"   • {opt1} × {opt2}: {action}")
                    if not dry_run:
                        client.update_variant_price(vid, price)
                else:
                    action = f"CREATE prezzo {price}"
                    logs.append(f"   • {opt1} × {opt2}: {action}")
                    if not dry_run:
                        client.create_variant(product_id, price, opt1, opt2, sku=variant_sku)

        st.success("Completato (vedi log).")
        st.text_area("Log", "\n".join(logs), height=300)
else:
    st.info("Carica il file o inserisci un URL RAW da GitHub per procedere.")
