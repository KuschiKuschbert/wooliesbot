import json
import os
import re

DATA_FILE = "docs/data.json"

BRANDS = [
    "Mission", "Nescafe", "Pics", "Western Star", "Wicked Sister", 
    "Ingham", "Connoisseur", "Quest", "BSC", "Campbells", "Philly",
    "Quilton", "Omo", "Jatz", "Lindt", "Pepsi", "Coke", "Wicked Sister",
    "Finish", "Fairy", "Colgate", "Oral B", "Head & Shoulders", "Pantene",
    "Palmolive", "San Remo", "Barilla", "Continental", "Maggi", "Sirena",
    "John West", "Uncle Tobys", "Kelloggs", "Sanitarium", "Bega", "Mainland"
]

SUB_MAP = {
    "fresh_protein": {
        r"chicken|thigh|breast|tenders|wing": "chicken",
        r"beef|rump|mince|steak|roast": "beef",
        r"pork|bacon|chorizo|ham|silverside|prosciutto|salami": "pork_deli",
        r"lamb|cutlet|shank": "lamb",
        r"fish|salmon|prawn|tuna|seafood": "seafood",
    },
    "fresh_veg": {
        r"capsicum|tomato|avocado|cucumber": "salad_veg",
        r"spinach|kale|lettuce|arugula": "leafy_greens",
        r"onion|garlic|shallot|leek": "alliums",
        r"potato|carrot|sweet potato|pumpkin|beetroot": "root_veg",
        r"broccoli|zucchini|mushroom|peas|corn|beans|cauliflower": "cooking_veg",
        r"apple|banana|berry|citrus|lemon|lime|melon|watermelon|grapes": "fruit",
        r"herbs|basil|parsley|coriander": "herbs",
    },
    "dairy": {
        r"milk": "milk",
        r"eggs": "eggs",
        r"butter|margarine": "butter",
        r"cheese|tasty|parmesan|mozzarella|feta|brie": "cheese",
        r"cream|sour cream|yogurt|yoghurt": "cream_yogurt",
    },
    "pantry": {
        r"pepsi|coke|coffee|tea|juice|water|soft drink": "beverages",
        r"muffin|wrap|bread|bagel|crumpet": "bakery",
        r"chocolate|bar|cracker|dates|chips|biscuits|nut": "snacks",
        r"canned|stock|sauce|oil|flour|sugar|spice|paste|gravy": "cooking_essentials",
        r"pasta|rice|noodle": "grains_pasta",
        r"cereal|oats|muesli": "breakfast",
    },
    "house": {
        r"toilet paper|paper towel": "paper_goods",
        r"detergent|laundry|dish|cleaner": "cleaning",
        r"soap|shampoo|toothpaste|brush": "personal_care",
    }
}

def enrich():
    with open(DATA_FILE, "r") as f:
        data = json.load(f)

    for item in data:
        name = item.get("name", "")
        
        # 1. Extract Brand
        brand = "Private Label"
        for b in BRANDS:
            if b.lower() in name.lower():
                brand = b
                break
        item["brand"] = brand

        # 2. Extract Subcategory
        main_type = item.get("type", "pantry")
        sub_type = "other"
        if main_type in SUB_MAP:
            for pattern, sub in SUB_MAP[main_type].items():
                if re.search(pattern, name, re.IGNORECASE):
                    sub_type = sub
                    break
        item["subcategory"] = sub_type

        # 3. Extract Volume/Size
        volume_match = re.search(r"(\d+(g|kg|ml|l|pk))", name, re.IGNORECASE)
        item["size"] = volume_match.group(1) if volume_match else "Standard"

        # 4. Tags
        tags = []
        if any(x in name.lower() for x in ["bulk", "24pk", "1kg"]):
            tags.append("bulk")
        if item.get("is_staple"):
            tags.append("staple")
        item["tags"] = tags

    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=4)
    
    print(f"Successfully enriched {len(data)} items.")

if __name__ == "__main__":
    enrich()
