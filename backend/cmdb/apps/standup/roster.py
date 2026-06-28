"""IS team roster — seeded from standup-dashboard's config.py (_SEED_ROSTER).

Static for now (DB overrides / deriving from the GitHub "Information Systems"
repo are later phases). Drives the Stand up board's region grouping.
Tuple shape: (name, email, regions, manager, global, starred, github)
"""
REGIONS = ["AMER", "APAC", "EMEA"]

# Region-local timezones (from standup-dashboard config.REGION_TIMEZONES) — define
# "today"/weekday for role resolution and the 24h windows.
REGION_TZ = {
    "AMER": "America/Mexico_City",
    "APAC": "Australia/Sydney",
    "EMEA": "Europe/Paris",
}

_R = [
    ("Fernando Carrillo", "fernando.carrillo.castro@canonical.com", ("AMER", "APAC"), True, False, False, "fercc17"),
    ("Alexandre Gomes", "alexandre.gomes@canonical.com", ("AMER",), False, False, False, "alejdg"),
    ("Colin Misare", "colin.misare@canonical.com", ("AMER",), False, False, False, "cmisare"),
    ("Matheus Carvalho", "matheus.carvalho@canonical.com", ("AMER",), False, False, False, "mcarvalhor"),
    ("Nikolaos Sakkos", "nikolaos.sakkos@canonical.com", ("AMER",), False, False, False, "nsakkos"),
    ("Alex Lukens", "alex.lukens@canonical.com", ("AMER",), False, False, False, "alexdlukens-canonical"),
    ("Afif Refrizal", "afif.refrizal@canonical.com", ("AMER",), False, False, False, "afiffahreza"),
    ("James Simpson", "james.simpson@canonical.com", ("APAC",), False, False, False, "jsimps"),
    ("Loic Gomez", "loic.gomez@canonical.com", ("APAC",), False, False, False, "kot0dama"),
    ("Paul Collins", "paul.collins@canonical.com", ("APAC",), False, False, False, "vmpjdc"),
    ("Haw Loeung", "haw.loeung@canonical.com", ("APAC",), False, False, False, "hloeung"),
    ("Barry Price", "barry.price@canonical.com", ("APAC",), False, False, True, "barryprice"),
    ("Javier Arregui", "javier.arregui@canonical.com", ("EMEA",), True, False, False, "javier-arregui"),
    ("Benjamin Allot", "benjamin.allot@canonical.com", ("EMEA",), False, False, False, "ben-ballot"),
    ("Gianluca Perna", "gianluca.perna@canonical.com", ("EMEA",), False, False, False, "gianlucaperna"),
    ("Christos Betzelos", "christos.betzelos@canonical.com", ("EMEA",), False, False, False, "chrisbetze"),
    ("Giorgos Apostolopoulos", "giorgos.apostolopoulos@canonical.com", ("EMEA",), False, False, False, "joj0s"),
    ("Junien Fridrick", "junien.fridrick@canonical.com", ("EMEA",), False, False, False, "axinojolais"),
    ("Laurent Sesques", "laurent.sesques@canonical.com", ("EMEA",), False, False, False, "sajoupa"),
    ("Kristofer Tingdahl", "kristofer.tingdahl@canonical.com", (), True, True, False, "tingdahl"),
    ("Alexandre Micouleau", "alexandre.micouleau@canonical.com", (), True, True, False, "alexmicouleau"),
]

ROSTER = [
    {"name": n, "email": e, "regions": list(rg), "manager": mgr,
     "global": glb, "starred": star, "github": gh}
    for (n, e, rg, mgr, glb, star, gh) in _R
]

BY_EMAIL = {e["email"]: e for e in ROSTER}
