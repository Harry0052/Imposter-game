"""
Content bank for Imposter Media.

Each `ContentPair` has a `normal` prompt for the majority and an `imposter`
prompt for the lone imposter. Pairs are thematically adjacent so the
imposter can plausibly bluff with a one-word answer, but different enough
that a careful group can spot the outlier.

Each pair is tagged with a `category` so the host can pick a theme from
the lobby (Influencers, Sports, Music, YouTube, TikTok, Memes, Movies,
Random objects, Pictures, Videos). `pick_random_pair()` honours the
chosen category; with no category it picks from the whole bank.
"""

import random
from typing import List, Optional

from game_logic import ContentPair


def _cp(normal: str, imposter: str, category: str, kind: str = "text") -> ContentPair:
    return ContentPair(normal=normal, imposter=imposter, kind=kind, category=category)


CONTENT_BANK: List[ContentPair] = [
    # --- Influencers ---
    _cp("MrBeast giving away money",            "Kai Cenat livestreaming with fans",       "influencers"),
    _cp("Logan Paul hosting his podcast",       "KSI performing on stage",                 "influencers"),
    _cp("Emma Chamberlain drinking coffee",     "Bella Poarch lip-syncing on TikTok",      "influencers"),
    _cp("Charli D'Amelio dancing",              "Addison Rae posing for a photo",          "influencers"),
    _cp("PewDiePie reacting to memes",          "Markiplier playing a horror game",        "influencers"),
    _cp("Jake Paul training for a fight",       "Ryan Garcia sparring in the gym",         "influencers"),

    # --- Sports ---
    _cp("LeBron James dunking a basketball",    "Stephen Curry shooting a three-pointer",  "sports"),
    _cp("Messi scoring a World Cup goal",       "Ronaldo celebrating a Champions League goal", "sports"),
    _cp("Serena Williams serving an ace",       "Rafael Nadal hitting a topspin forehand", "sports"),
    _cp("Tom Brady throwing a touchdown",       "Patrick Mahomes scrambling in the pocket","sports"),
    _cp("Tiger Woods sinking a long putt",      "Rory McIlroy driving off the tee",        "sports"),
    _cp("Usain Bolt winning the 100m",          "Michael Phelps finishing a swim race",    "sports"),

    # --- Music ---
    _cp("Taylor Swift on her Eras Tour",        "Beyoncé on her Renaissance Tour",         "music"),
    _cp("Drake performing in Toronto",          "The Weeknd performing at Coachella",      "music"),
    _cp("Billie Eilish at the Grammys",         "Olivia Rodrigo at the VMAs",              "music"),
    _cp("Ed Sheeran playing acoustic guitar",   "Shawn Mendes singing at an arena",        "music"),
    _cp("Kendrick Lamar freestyling",           "J. Cole performing a verse live",         "music"),
    _cp("Bruno Mars in a tuxedo at a show",     "The Weeknd in sunglasses on stage",       "music"),

    # --- YouTube ---
    _cp("Mark Rober building a Rube Goldberg machine",  "Veritasium explaining a physics problem", "youtube"),
    _cp("Dude Perfect doing a trick shot",      "How Ridiculous dropping stuff off a tower","youtube"),
    _cp("Ryan Trahan traveling for a challenge","Airrack pulling off a stunt",             "youtube"),
    _cp("Casey Neistat riding a Boosted board", "Peter McKinnon showing a photo edit",     "youtube"),
    _cp("James Charles doing a makeup tutorial","NikkieTutorials creating an eye look",    "youtube"),
    _cp("Unbox Therapy reviewing a gadget",     "Linus Tech Tips building a PC",           "youtube"),

    # --- TikTok ---
    _cp("A couple doing a dance trend",         "Friends doing a cooking trend",           "tiktok"),
    _cp("Someone doing 'get ready with me'",    "Someone doing 'day in my life'",          "tiktok"),
    _cp("A BookTok recommendation",             "A FoodTok recipe hack",                   "tiktok"),
    _cp("Khaby Lame silently reacting",         "Bella Poarch lip-syncing",                "tiktok"),
    _cp("A storytime about a bad date",         "A storytime about a weird coworker",      "tiktok"),
    _cp("A POV skit at school",                 "A POV skit at the grocery store",         "tiktok"),

    # --- Memes ---
    _cp("Distracted boyfriend meme",            "Two buttons meme",                        "memes"),
    _cp("Drake approving meme",                 "Surprised Pikachu meme",                  "memes"),
    _cp("Gigachad meme",                        "Chad Yes vs Virgin No meme",              "memes"),
    _cp("Stonks meme",                          "This is fine dog meme",                   "memes"),
    _cp("Expanding brain meme",                 "Galaxy brain meme",                       "memes"),
    _cp("Woman yelling at cat meme",            "Evil Kermit meme",                        "memes"),

    # --- Movies ---
    _cp("Iron Man snapping his fingers",        "Thor swinging his hammer",                "movies"),
    _cp("Harry Potter casting a spell",         "Hermione brewing a potion",               "movies"),
    _cp("Darth Vader wielding a lightsaber",    "Luke Skywalker piloting an X-wing",       "movies"),
    _cp("Frodo carrying the One Ring",          "Aragorn leading a charge",                "movies"),
    _cp("Batman brooding on a rooftop",         "Spider-Man swinging between buildings",   "movies"),
    _cp("The Joker laughing maniacally",        "The Riddler leaving a clue",              "movies"),
    _cp("Shrek in a swamp",                     "Po from Kung Fu Panda eating dumplings",  "movies"),

    # --- Random objects ---
    _cp("A slice of pepperoni pizza",           "A cheeseburger with fries",               "random_objects"),
    _cp("The Eiffel Tower at night",            "The Statue of Liberty in the morning",    "random_objects"),
    _cp("A golden retriever puppy",             "A tabby kitten playing with yarn",        "random_objects"),
    _cp("A roller coaster at Disney World",     "A Ferris wheel at a county fair",         "random_objects"),
    _cp("An iPhone launch event",               "A Samsung Galaxy unveiling",              "random_objects"),
    _cp("A steaming mug of coffee",             "A bubble tea with pearls",                "random_objects"),

    # --- Pictures (real image URLs) ---
    _cp(normal="https://images.unsplash.com/photo-1514888286974-6c03e2ca1dba?w=600",
        imposter="https://images.unsplash.com/photo-1533738363-b7f9aef128ce?w=600",
        category="pictures", kind="image"),
    _cp(normal="https://images.unsplash.com/photo-1504384308090-c894fdcc538d?w=600",
        imposter="https://images.unsplash.com/photo-1498050108023-c5249f4df085?w=600",
        category="pictures", kind="image"),

    # --- Videos (text descriptions for now; swap in URLs later if you host video) ---
    _cp("A dramatic slow-motion goal replay",   "A slow-motion diving catch replay",       "videos"),
    _cp("A fireworks show finale",              "A drone light-show performance",          "videos"),
    _cp("A stunt car drifting around a corner", "A rally car jumping over a hill",         "videos"),
]


def pick_random_pair(category: Optional[str] = None) -> ContentPair:
    """Pick a content pair at random.

    - `category=None`: draw from the full bank.
    - `category="influencers"` etc.: draw only pairs with that tag. Falls
      back to the full bank if a category somehow has no pairs (shouldn't
      happen, but belt-and-braces).
    """
    if category:
        pool = [p for p in CONTENT_BANK if p.category == category]
        if pool:
            return random.choice(pool)
    return random.choice(CONTENT_BANK)
