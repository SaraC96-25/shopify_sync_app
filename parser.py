
import pandas as pd

def parse_pricing_matrix(df: pd.DataFrame) -> pd.DataFrame:
    """
    Trasforma il foglio Excel nel formato lungo:
    colonne finali: ["Titolo Prodotto","SKU","Posizione Stampa","Quantità","Prezzo"]
    Logica specifica per il file fornito:
    - La prima riga del blocco per ciascun prodotto contiene solo i breakpoints di quantità (nella colonna "Quantità" e nelle colonne Unnamed).
    - Le righe successive del blocco contengono le varie "Posizione Stampa" e i prezzi corrispondenti alle quantità.
    - "Titolo Prodotto" e "SKU" compaiono solo nella prima riga del blocco e vanno propagati verso il basso fino al prossimo prodotto.
    """
    # Rimuove colonne vuote e rinomina
    df = df.copy()
    non_empty_cols = [c for c in df.columns if not df[c].isna().all()]
    df = df[non_empty_cols]

    # Identifica la riga "header delle quantità": è la riga in cui "Posizione Stampa" è NaN.
    # In realtà nel tuo file c'è UNA riga header per l'intero sheet e poi per OGNI PRODOTTO
    # appaiono le righe con Posizione Stampa valorizzata. Usiamo una strategia robusta:
    # - Ogni volta che troviamo "Titolo Prodotto" non nullo, inizia un nuovo blocco prodotto.
    # - La riga precedente del blocco (quando Posizione Stampa è NaN) contiene i valori quantità.
    rows = []
    i = 0
    n = len(df)
    while i < n:
        row = df.iloc[i]
        if pd.notna(row.get("Titolo Prodotto")) and pd.notna(row.get("SKU")) and pd.isna(row.get("Posizione Stampa")):
            # Questa è la riga di QUANTITÀ per il nuovo prodotto
            titolo = row["Titolo Prodotto"]
            sku = row["SKU"]

            # Estrai le quantita' da questa riga (tutte le colonne numeriche a destra di "Posizione Stampa")
            # Individua le colonne quantità: dal campo "Quantità" in poi
            if "Quantità" not in df.columns:
                raise ValueError("Colonna 'Quantità' non trovata nel file.")

            qty_cols = []
            start_collect = False
            for c in df.columns:
                if c == "Quantità":
                    start_collect = True
                if start_collect:
                    qty_cols.append(c)

            qty_values = row[qty_cols]
            # Filtra valori numerici e convertili in int (es. 1,2,3,...)
            quantities = []
            for c in qty_cols:
                val = row[c]
                if pd.notna(val):
                    try:
                        q = int(float(val))
                        quantities.append((c, q))
                    except Exception:
                        pass

            # Ora scorri le righe successive finché non trovi un'altra riga con Titolo Prodotto (nuovo blocco) o fine file
            j = i + 1
            while j < n:
                r2 = df.iloc[j]
                if pd.notna(r2.get("Titolo Prodotto")) and pd.notna(r2.get("SKU")):
                    # è l'inizio del prossimo blocco
                    break

                pos = r2.get("Posizione Stampa")
                if pd.notna(pos):
                    # Estrai i prezzi su tutte le quantità
                    for (colname, q) in quantities:
                        price_val = r2.get(colname, None)
                        if pd.notna(price_val):
                            try:
                                prezzo = float(price_val)
                            except Exception:
                                continue
                            rows.append({
                                "Titolo Prodotto": titolo,
                                "SKU": sku,
                                "Posizione Stampa": str(pos).strip(),
                                "Quantità": int(q),
                                "Prezzo": round(prezzo, 2),
                            })
                j += 1

            # Avanza l'indice al prossimo blocco
            i = j
        else:
            i += 1

    result = pd.DataFrame(rows)
    # Ordina
    if not result.empty:
        result = result.sort_values(["Titolo Prodotto","SKU","Posizione Stampa","Quantità"]).reset_index(drop=True)
    return result
