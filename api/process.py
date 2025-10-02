# api/process.py
import io
import json
import os
import tempfile
import requests

def handler(request):
    try:
        body = {}
        try:
            body = json.loads(request.body.decode() if hasattr(request, "body") else request.get_data().decode())
        except Exception:
            pass

        # Expect either {"url": "..."} or form-data.
        file_url = body.get("url")
        if not file_url:
            return ("application/json", 400, json.dumps({"error":"missing url"}))

        # Download file into temp file
        r = requests.get(file_url, headers={"User-Agent":"agri-processor/1.0"}, timeout=20)
        r.raise_for_status()

        fd, tmp_path = tempfile.mkstemp(suffix=".docx")
        os.close(fd)
        with open(tmp_path, "wb") as f:
            f.write(r.content)

        # Call utils; parse_amendments expects a path
        try:
            from utils import parse_amendments, create_amendment_report
            from utils import remove_unnec_tags, extract_additions, extract_deletions
        except Exception:
            # cleanup then return error
            os.remove(tmp_path)
            return ("application/json", 500, json.dumps({"error":"utils import failed"}))

        try:
            parsed = parse_amendments(tmp_path)            # returns dict expected by create_amendment_report
            report_df = create_amendment_report(parsed)    # Pandas DataFrame
        except Exception as e:
            os.remove(tmp_path)
            return ("application/json", 500, json.dumps({"error":"processing failed", "detail": str(e)}))

        # Post-process DataFrame columns as you showed
        try:
            report_df['Resumen'] = ''
            report_df['Original'] = ''
            report_df['Elimina'] = ''
            report_df['Añade'] = ''
            for i, a in enumerate(parsed.get('amendments', {}).values()):
                if 'Propuesta de rechazo' in a.keys():
                    report_df.loc[i, 'Resumen'] = 'rechazada'
                else:
                    try:
                        A = remove_unnec_tags(a.get('Amended',''))
                        report_df.loc[i, 'Añade'] = extract_additions(A)
                    except Exception:
                        report_df.loc[i, 'Añade'] = "(Comprobar!!)"
                    try:
                        C = remove_unnec_tags(a.get('Original',''))
                        report_df.loc[i, 'Elimina'] = extract_deletions(C)
                        report_df.loc[i, 'Original'] = a.get('OriginalType','')
                    except Exception:
                        report_df.loc[i, 'Elimina'] = "(Comprobar!!)"
        except Exception:
            # If your helper functions (remove_unnec_tags, extract_additions, ...) live in utils,
            # import and call them instead; I only show the pattern here.
            pass

        # Export DataFrame to bytes as XLSX
        out = io.BytesIO()
        try:
            import pandas as pd
            with pd.ExcelWriter(out, engine="openpyxl") as writer:
                report_df.to_excel(writer, index=False, sheet_name="Report")
            out.seek(0)
            b = out.read()
        except Exception as e:
            os.remove(tmp_path)
            return ("application/json", 500, json.dumps({"error":"excel export failed", "detail": str(e)}))

        # Cleanup temp file
        os.remove(tmp_path)

        # Return XLSX as attachment
        headers = {
            "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "Content-Disposition": 'attachment; filename="amendments_report.xlsx"'
        }
        # Some adapters expect a Response object; this tuple style is consistent with other examples
        return (headers["Content-Type"], 200, b)
    except Exception as e:
        return ("application/json", 500, json.dumps({"error":"unexpected", "detail": str(e)}))
