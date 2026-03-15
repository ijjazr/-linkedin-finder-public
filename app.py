import csv
import io
import json
import os
import re
from datetime import date

import gspread
import streamlit as st
from dotenv import load_dotenv
from google.oauth2.service_account import Credentials

from serpapi import GoogleSearch

# ── Config ───────────────────────────────────────────────────────────────────

load_dotenv()

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# ── SerpAPI Search ───────────────────────────────────────────────────────────


def search_linkedin(titles, location, industry, seniority, keywords, exclude, max_results=100):
    """Search for multiple job titles via SerpAPI, combine and deduplicate."""
    title_list = [t.strip() for t in titles.strip().splitlines() if t.strip()]
    if not title_list:
        return [], ""

    exclude_parts = []
    if exclude:
        for ex in exclude.split(","):
            ex = ex.strip()
            if ex:
                exclude_parts.append(f'-"{ex}"')

    results = []
    seen_urls = set()
    per_title_limit = max(10, max_results // len(title_list))

    for title in title_list:
        if len(results) >= max_results:
            break

        parts = ["site:linkedin.com/in", f'"{title}"']
        for term in [location, industry, seniority, keywords]:
            if term:
                parts.append(f'"{term}"')
        parts.extend(exclude_parts)

        query = " ".join(parts)

        for start in range(0, per_title_limit, 10):
            if len(results) >= max_results:
                break
            params = {
                "engine": "google",
                "q": query,
                "api_key": st.session_state.get("serpapi_key", ""),
                "num": 10,
                "start": start,
            }
            search = GoogleSearch(params)
            data = search.get_dict()

            items = data.get("organic_results", [])
            if not items:
                break

            for item in items:
                parsed = _parse_result(item)
                if parsed and parsed["LinkedIn URL"] not in seen_urls:
                    seen_urls.add(parsed["LinkedIn URL"])
                    results.append(parsed)

    combined_query = " | ".join(title_list)
    return results[:max_results], combined_query


def _parse_result(item):
    """Extract name, title, company, and URL from a search result."""
    url = item.get("link", "")
    if "/in/" not in url:
        return None

    raw_title = item.get("title", "")
    raw_title = raw_title.replace(" | LinkedIn", "").strip()
    segments = [s.strip() for s in re.split(r"\s[–—-]\s", raw_title)]

    name = segments[0] if segments else ""
    title = segments[1] if len(segments) > 1 else ""
    company = segments[2] if len(segments) > 2 else ""

    return {
        "Name": name,
        "Title": title,
        "Company": company,
        "LinkedIn URL": url,
    }


# ── Google Sheets Export ─────────────────────────────────────────────────────


def export_to_sheet(rows, query, sheet_id, creds_info):
    """Append rows to a user's Google Sheet, skipping duplicates."""
    creds = Credentials.from_service_account_info(creds_info, scopes=SCOPES)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(sheet_id)
    ws = sh.sheet1

    existing = ws.get_all_values()
    if not existing:
        ws.update("A1", [["Name", "Title", "Company", "LinkedIn URL", "Search Query", "Date"]])
        existing_urls = set()
    else:
        url_col = 3
        existing_urls = {r[url_col] for r in existing[1:] if len(r) > url_col}

    new_rows = []
    today = date.today().isoformat()
    for r in rows:
        if r["LinkedIn URL"] not in existing_urls:
            new_rows.append([
                r["Name"], r["Title"], r["Company"],
                r["LinkedIn URL"], query, today,
            ])

    if new_rows:
        next_row = len(existing) + 1
        ws.update(f"A{next_row}", new_rows, value_input_option="USER_ENTERED")

    return len(new_rows)


# ── Streamlit UI ─────────────────────────────────────────────────────────────

st.set_page_config(page_title="LinkedIn Profile Finder", layout="wide")
st.title("LinkedIn Profile Finder")

with st.expander("Setup Guide — Read this first"):
    st.markdown("""
### Step 1: SerpAPI Key (required for search)
1. Go to [serpapi.com](https://serpapi.com/) and create a free account
2. Copy your API key from the dashboard
3. Paste it in the sidebar under **SerpAPI Key**
4. Free tier gives **100 searches/month**

### Step 2: Google Sheets Export (optional)
1. Go to [Google Cloud Console](https://console.cloud.google.com/) and create a project
2. Enable **Google Sheets API** and **Google Drive API** (APIs & Services > Library)
3. Create a **Service Account** (IAM & Admin > Service Accounts)
4. Download the **JSON key**: click your service account > Keys > Add Key > JSON
5. Create a **Google Sheet** and copy the ID from the URL:
   `docs.google.com/spreadsheets/d/`**THIS_PART**`/edit`
6. **Share the Sheet** with your service account email as **Editor**
7. In the sidebar: paste your **Sheet ID** and upload your **credentials.json**

**Download CSV** works without any setup.
""")

# Session state
if "saved_searches" not in st.session_state:
    st.session_state.saved_searches = {}
if "results" not in st.session_state:
    st.session_state.results = []
if "last_query" not in st.session_state:
    st.session_state.last_query = ""
if "creds_info" not in st.session_state:
    st.session_state.creds_info = None

# ── Sidebar ──────────────────────────────────────────────────────────────────

with st.sidebar:
    st.header("API Key")
    serpapi_key = st.text_input(
        "SerpAPI Key",
        value=st.session_state.get("serpapi_key", ""),
        type="password",
        help="Get a free key at serpapi.com (100 searches/month)",
    )
    if serpapi_key:
        st.session_state.serpapi_key = serpapi_key

    st.divider()
    st.header("Google Sheet Connection")

    sheet_id = st.text_input(
        "Google Sheet ID",
        value=st.session_state.get("sheet_id", ""),
        help="From your Sheet URL: docs.google.com/spreadsheets/d/THIS_PART/edit",
    )
    if sheet_id:
        st.session_state.sheet_id = sheet_id

    creds_file = st.file_uploader(
        "Upload credentials.json",
        type=["json"],
        help="Your Google service account JSON key file",
    )
    if creds_file:
        st.session_state.creds_info = json.load(creds_file)
        st.success("Credentials loaded!")

    if st.session_state.creds_info:
        sa_email = st.session_state.creds_info.get("client_email", "")
        st.info(f"Share your Sheet with:\n`{sa_email}`\nas **Editor**")

    st.divider()
    st.header("Saved Searches")
    names = list(st.session_state.saved_searches.keys())
    if names:
        selected = st.selectbox("Load a saved search", [""] + names)
        if st.button("Load") and selected:
            st.session_state.update(st.session_state.saved_searches[selected])
            st.rerun()
    else:
        st.info("No saved searches yet.")

# ── Search Form ──────────────────────────────────────────────────────────────

col1, col2 = st.columns(2)
with col1:
    job_title = st.text_area(
        "Job Titles (one per line)",
        value=st.session_state.get("job_title", ""),
        height=120,
        placeholder="Head of Revenue Operations\nDirector of Revenue Operations\nVP Revenue Operations",
    )
    location = st.text_input("Location", value=st.session_state.get("location", ""))
    industry = st.text_input("Industry", value=st.session_state.get("industry", ""))
with col2:
    seniority = st.selectbox(
        "Seniority",
        ["", "Intern", "Junior", "Mid-level", "Senior", "Lead", "Director", "VP", "C-level"],
        index=0,
    )
    keywords = st.text_input("Additional Keywords", value=st.session_state.get("keywords", ""))
    exclude = st.text_input("Exclude (comma-separated)", value=st.session_state.get("exclude", ""))
    max_results = st.slider("Max results", min_value=10, max_value=100, value=50, step=10,
                            help="Each 10 results uses 1 API credit")

# ── Search ───────────────────────────────────────────────────────────────────

if st.button("Search", type="primary"):
    if not st.session_state.get("serpapi_key"):
        st.error("Enter your SerpAPI key in the sidebar. Get a free key at serpapi.com")
    elif not job_title:
        st.warning("Please enter at least a Job Title.")
    else:
        with st.spinner("Searching…"):
            try:
                results, query = search_linkedin(
                    job_title, location, industry, seniority, keywords, exclude,
                    max_results=max_results,
                )
                st.session_state.results = results
                st.session_state.last_query = query
            except Exception as e:
                st.error(f"Search failed: {e}")

# ── Results ──────────────────────────────────────────────────────────────────

if st.session_state.results:
    st.subheader(f"Results ({len(st.session_state.results)})")
    st.dataframe(
        st.session_state.results,
        column_config={"LinkedIn URL": st.column_config.LinkColumn()},
        use_container_width=True,
    )

    col_export, col_csv = st.columns(2)

    with col_csv:
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=["Name", "Title", "Company", "LinkedIn URL"])
        writer.writeheader()
        writer.writerows(st.session_state.results)
        st.download_button("Download CSV", buf.getvalue(), "linkedin_results.csv", "text/csv")

    with col_export:
        if st.button("Export to Google Sheet"):
            if not st.session_state.get("sheet_id"):
                st.error("Enter your Google Sheet ID in the sidebar.")
            elif not st.session_state.creds_info:
                st.error("Upload your credentials.json in the sidebar.")
            else:
                with st.spinner("Exporting…"):
                    try:
                        added = export_to_sheet(
                            st.session_state.results,
                            st.session_state.last_query,
                            st.session_state.sheet_id,
                            st.session_state.creds_info,
                        )
                        st.success(f"Exported {added} new profile(s) to Google Sheets.")
                    except Exception as e:
                        st.error(f"Export failed: {e}")

    with st.expander("Save This Search"):
        save_name = st.text_input("Search name")
        if st.button("Save") and save_name:
            st.session_state.saved_searches[save_name] = {
                "job_title": job_title,
                "location": location,
                "industry": industry,
                "keywords": keywords,
                "exclude": exclude,
            }
            st.success(f"Saved search '{save_name}'.")
elif st.session_state.last_query:
    st.info("No results found. Try broader search terms.")
