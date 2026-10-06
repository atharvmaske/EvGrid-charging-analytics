import csv
from config.cities import CITIES

with open("dbt/seeds/cities.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f, lineterminator="\n")
    w.writerow(["city", "country_code", "timezone"])
    for name, c in CITIES.items():
        w.writerow([name, c["country_code"], c["timezone"]])
print(f"Wrote {len(CITIES)} cities")
