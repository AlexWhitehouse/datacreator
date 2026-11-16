"""Static UK reference data used by the built-in generators.

Towns carry their postcode area, a district range and dialling code so that a
generated address, postcode and landline number agree with each other.
"""

# (post town, county, postcode area, min district, max district, dialling code)
TOWNS = [
    ("London", "Greater London", "E", 1, 18, "020"),
    ("London", "Greater London", "N", 1, 22, "020"),
    ("London", "Greater London", "SE", 1, 28, "020"),
    ("London", "Greater London", "SW", 1, 20, "020"),
    ("Birmingham", "West Midlands", "B", 1, 48, "0121"),
    ("Manchester", "Greater Manchester", "M", 1, 40, "0161"),
    ("Leeds", "West Yorkshire", "LS", 1, 29, "0113"),
    ("Sheffield", "South Yorkshire", "S", 1, 36, "0114"),
    ("Liverpool", "Merseyside", "L", 1, 38, "0151"),
    ("Bristol", "Bristol", "BS", 1, 16, "0117"),
    ("Nottingham", "Nottinghamshire", "NG", 1, 22, "0115"),
    ("Leicester", "Leicestershire", "LE", 1, 9, "0116"),
    ("Newcastle upon Tyne", "Tyne and Wear", "NE", 1, 15, "0191"),
    ("Sunderland", "Tyne and Wear", "SR", 1, 6, "0191"),
    ("Bradford", "West Yorkshire", "BD", 1, 15, "01274"),
    ("York", "North Yorkshire", "YO", 10, 32, "01904"),
    ("Hull", "East Riding of Yorkshire", "HU", 1, 17, "01482"),
    ("Coventry", "West Midlands", "CV", 1, 6, "024"),
    ("Wolverhampton", "West Midlands", "WV", 1, 14, "01902"),
    ("Stoke-on-Trent", "Staffordshire", "ST", 1, 12, "01782"),
    ("Derby", "Derbyshire", "DE", 1, 24, "01332"),
    ("Southampton", "Hampshire", "SO", 14, 19, "023"),
    ("Portsmouth", "Hampshire", "PO", 1, 6, "023"),
    ("Reading", "Berkshire", "RG", 1, 6, "0118"),
    ("Oxford", "Oxfordshire", "OX", 1, 4, "01865"),
    ("Cambridge", "Cambridgeshire", "CB", 1, 5, "01223"),
    ("Norwich", "Norfolk", "NR", 1, 7, "01603"),
    ("Ipswich", "Suffolk", "IP", 1, 5, "01473"),
    ("Brighton", "East Sussex", "BN", 1, 3, "01273"),
    ("Exeter", "Devon", "EX", 1, 4, "01392"),
    ("Plymouth", "Devon", "PL", 1, 9, "01752"),
    ("Gloucester", "Gloucestershire", "GL", 1, 4, "01452"),
    ("Cheltenham", "Gloucestershire", "GL", 50, 53, "01242"),
    ("Bath", "Somerset", "BA", 1, 2, "01225"),
    ("Swindon", "Wiltshire", "SN", 1, 5, "01793"),
    ("Northampton", "Northamptonshire", "NN", 1, 7, "01604"),
    ("Milton Keynes", "Buckinghamshire", "MK", 1, 15, "01908"),
    ("Luton", "Bedfordshire", "LU", 1, 4, "01582"),
    ("Peterborough", "Cambridgeshire", "PE", 1, 7, "01733"),
    ("Lincoln", "Lincolnshire", "LN", 1, 6, "01522"),
    ("Preston", "Lancashire", "PR", 1, 5, "01772"),
    ("Blackpool", "Lancashire", "FY", 1, 4, "01253"),
    ("Bolton", "Greater Manchester", "BL", 1, 7, "01204"),
    ("Stockport", "Greater Manchester", "SK", 1, 8, "0161"),
    ("Wigan", "Greater Manchester", "WN", 1, 6, "01942"),
    ("Warrington", "Cheshire", "WA", 1, 5, "01925"),
    ("Chester", "Cheshire", "CH", 1, 4, "01244"),
    ("Middlesbrough", "North Yorkshire", "TS", 1, 8, "01642"),
    ("Durham", "County Durham", "DH", 1, 1, "0191"),
    ("Carlisle", "Cumbria", "CA", 1, 3, "01228"),
    ("Lancaster", "Lancashire", "LA", 1, 2, "01524"),
    ("Huddersfield", "West Yorkshire", "HD", 1, 5, "01484"),
    ("Wakefield", "West Yorkshire", "WF", 1, 4, "01924"),
    ("Doncaster", "South Yorkshire", "DN", 1, 12, "01302"),
    ("Canterbury", "Kent", "CT", 1, 4, "01227"),
    ("Maidstone", "Kent", "ME", 14, 17, "01622"),
    ("Guildford", "Surrey", "GU", 1, 5, "01483"),
    ("Chelmsford", "Essex", "CM", 1, 3, "01245"),
    ("Colchester", "Essex", "CO", 1, 4, "01206"),
    ("Bournemouth", "Dorset", "BH", 1, 11, "01202"),
    ("Worcester", "Worcestershire", "WR", 1, 5, "01905"),
    ("Shrewsbury", "Shropshire", "SY", 1, 5, "01743"),
    ("Telford", "Shropshire", "TF", 1, 7, "01952"),
    ("Cardiff", "South Glamorgan", "CF", 3, 24, "029"),
    ("Swansea", "West Glamorgan", "SA", 1, 7, "01792"),
    ("Edinburgh", "Midlothian", "EH", 1, 17, "0131"),
    ("Glasgow", "Lanarkshire", "G", 1, 53, "0141"),
    ("Aberdeen", "Aberdeenshire", "AB", 10, 25, "01224"),
    ("Belfast", "County Antrim", "BT", 1, 17, "028"),
]

# Letters valid in the inward part of a UK postcode.
POSTCODE_INWARD_LETTERS = "ABDEFGHJLNPQRSTUWXYZ"

LOCALITIES = [
    "Westfield", "Oakwood", "Hillside", "Moorside", "Brookside", "Fairfield",
    "Springfield", "Highfield", "Parkside", "Woodlands", "Meadowbank", "Ashgrove",
    "Northfield", "Kingsway", "Riverside", "Southgate", "Eastville", "Greenhill",
]

BUILDING_NAMES = [
    "Rose Court", "Victoria House", "Mill View", "Albion Court", "Orchard House",
    "Kingfisher Court", "Elm Lodge", "St John's Court", "Beech House", "Harbour View",
]

EMAIL_DOMAINS = [
    "gmail.com", "outlook.com", "hotmail.co.uk", "yahoo.co.uk", "btinternet.com",
    "icloud.com", "live.co.uk", "sky.com",
]

RELATIONSHIPS = [
    "Spouse", "Husband", "Wife", "Partner", "Son", "Daughter", "Mother", "Father",
    "Brother", "Sister", "Friend", "Neighbour", "Carer", "Grandson", "Granddaughter",
]

MARITAL_STATUSES = ["Single", "Married", "Civil partnership", "Divorced", "Widowed", "Separated"]

ETHNICITIES = [
    "White - British", "White - Irish", "White - Any other White background",
    "Mixed - White and Black Caribbean", "Mixed - White and Asian",
    "Asian or Asian British - Indian", "Asian or Asian British - Pakistani",
    "Asian or Asian British - Bangladeshi", "Black or Black British - Caribbean",
    "Black or Black British - African", "Chinese", "Any other ethnic group", "Not stated",
]

# (drug, typical dose)
MEDICATIONS = [
    ("Amlodipine", "5mg once daily"), ("Atorvastatin", "20mg at night"),
    ("Simvastatin", "40mg at night"), ("Metformin", "500mg twice daily"),
    ("Ramipril", "5mg once daily"), ("Lisinopril", "10mg once daily"),
    ("Bisoprolol", "2.5mg once daily"), ("Omeprazole", "20mg once daily"),
    ("Lansoprazole", "30mg once daily"), ("Levothyroxine", "100 micrograms once daily"),
    ("Salbutamol", "100 micrograms 2 puffs as required"), ("Sertraline", "50mg once daily"),
    ("Citalopram", "20mg once daily"), ("Paracetamol", "1g four times daily"),
    ("Ibuprofen", "400mg three times daily"), ("Co-codamol", "30/500 two tablets four times daily"),
    ("Furosemide", "40mg in the morning"), ("Apixaban", "5mg twice daily"),
    ("Rivaroxaban", "20mg once daily"), ("Warfarin", "as per INR"),
    ("Clopidogrel", "75mg once daily"), ("Aspirin", "75mg once daily"),
    ("Gabapentin", "300mg three times daily"), ("Amitriptyline", "10mg at night"),
    ("Prednisolone", "5mg once daily"), ("Insulin glargine", "20 units at night"),
    ("Methotrexate", "15mg once weekly"), ("Adalimumab", "40mg every other week"),
    ("Enoxaparin", "40mg once daily"), ("Ondansetron", "4mg as required"),
    ("Morphine sulfate", "10mg every 4 hours as required"), ("Doxycycline", "100mg once daily"),
    ("Amoxicillin", "500mg three times daily"), ("Ceftriaxone", "2g once daily IV"),
    ("Teicoplanin", "400mg once daily IV"), ("Ertapenem", "1g once daily IV"),
    ("Parenteral nutrition", "as per regimen"), ("Sodium chloride 0.9%", "1L over 8 hours"),
]

CONDITIONS = [
    "Type 2 diabetes", "Type 1 diabetes", "Hypertension", "Asthma", "COPD",
    "Atrial fibrillation", "Heart failure", "Chronic kidney disease", "Osteoarthritis",
    "Rheumatoid arthritis", "Crohn's disease", "Ulcerative colitis", "Multiple sclerosis",
    "Parkinson's disease", "Epilepsy", "Depression", "Anxiety", "Hypothyroidism",
    "Osteoporosis", "Cellulitis", "Osteomyelitis", "Short bowel syndrome",
    "Psoriasis", "Cystic fibrosis", "Dementia", "Stroke", "Angina", "Migraine",
    "Gout", "Anaemia",
]

ALLERGIES = [
    "NKDA", "NKDA", "NKDA", "NKDA", "Penicillin", "Penicillin (rash)", "Codeine",
    "Latex", "Sulfonamides", "Aspirin", "Ibuprofen", "Trimethoprim", "Erythromycin",
    "Peanuts", "Shellfish", "Elastoplast",
]

GP_PRACTICE_PATTERNS = [
    "{town} Medical Centre", "{locality} Surgery", "The {locality} Practice",
    "{locality} Health Centre", "{street} Surgery", "{locality} Medical Practice",
    "{town} Family Practice",
]

# Words the label-suggester treats as noise.
LABEL_STOPWORDS = {"please", "print", "block", "capitals", "in", "the", "of", "your", "patient's", "patients"}

STREET_PREFIXES = [
    "Church", "Station", "Victoria", "Park", "Mill", "High", "Queen's", "King's", "Manor",
    "Green", "School", "Chapel", "Albert", "York", "Grange", "Windsor", "Highfield",
    "Springfield", "Orchard", "Meadow", "Oak", "Elm", "Beech", "Ash", "Willow", "Cedar",
    "New", "North", "South", "West", "Hill", "Main", "Bridge", "Castle", "Market", "Westfield",
]
STREET_SUFFIXES = [
    ("Road", 30), ("Street", 20), ("Lane", 12), ("Avenue", 8), ("Close", 8), ("Drive", 6),
    ("Way", 4), ("Crescent", 4), ("Gardens", 3), ("Grove", 3), ("Place", 2), ("Terrace", 2),
    ("Court", 2), ("View", 1), ("Rise", 1), ("Walk", 1), ("Mews", 1),
]
