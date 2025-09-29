from flask import Flask, request, jsonify

app = Flask(__name__)

@app.route("/api/test", methods=["POST"])
def square():
    data = request.get_json(silent=True) or {}
    # Accept either {"n": 5} or form field "n"
    n = data.get("n") if isinstance(data, dict) else None
    try:
        if n is None:
            n = request.form.get("n", None)
        n = float(n)
    except Exception:
        return jsonify({"error": "invalid number"}), 400
    return jsonify({"result": n * n})

