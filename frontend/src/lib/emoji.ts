// Emoji you can find by name in the category emoji picker: each with the words it answers to. Leaning towards what a
// spending category might be; any other emoji can still be typed or pasted (or picked from the device's emoji keyboard).
export const EMOJI_WORDS: [string, string][] = [
  // food and drink
  ["🛒", "groceries grocery cart shopping supermarket food"], ["🍽️", "restaurants dining dinner plate eating out food"],
  ["🍔", "fast food burger hamburger takeout"], ["🍕", "pizza takeout food"], ["🌮", "taco mexican food"],
  ["🍣", "sushi japanese food"], ["🍜", "noodles ramen soup asian food"], ["🥗", "salad healthy food lunch"],
  ["🥪", "sandwich lunch deli"], ["🥡", "takeout takeaway delivery chinese food"], ["🍳", "breakfast cooking eggs kitchen"],
  ["🥐", "bakery croissant pastry breakfast"], ["🍩", "donut doughnut snacks sweets"], ["🍰", "cake dessert sweets bakery"],
  ["🍦", "ice cream dessert sweets"], ["🍫", "chocolate candy sweets snacks"], ["🍿", "popcorn snacks movies"],
  ["🍎", "apple fruit produce food"], ["🥦", "vegetables produce broccoli food"], ["🥩", "meat butcher steak"],
  ["☕", "coffee cafe tea snacks"], ["🧋", "bubble tea boba drinks"], ["🥤", "soda drinks soft drink cup"],
  ["🍷", "wine alcohol drinks bar"], ["🍺", "beer alcohol drinks bar pub"], ["🍸", "cocktail alcohol drinks bar"],
  ["🥃", "whiskey liquor alcohol drinks"], ["🍾", "champagne celebration alcohol party"], ["🧃", "juice drinks kids"],
  // shopping
  ["🛍️", "shopping bags retail purchases"], ["👕", "clothing clothes shirt apparel"], ["👗", "dress clothing clothes fashion"],
  ["👟", "shoes sneakers footwear"], ["👠", "shoes heels footwear fashion"], ["👜", "handbag purse accessories fashion"],
  ["🕶️", "sunglasses accessories"], ["💍", "jewelry ring wedding engagement"], ["⌚", "watch accessories jewelry"],
  ["🧴", "toiletries lotion personal care"], ["🪒", "razor shaving personal care"], ["💄", "makeup cosmetics beauty"],
  ["💅", "nails beauty salon personal care"], ["💇", "haircut hair salon barber personal care"], ["🧖", "spa sauna wellness"],
  ["📦", "package shipping amazon delivery mail"], ["🏬", "department store mall shopping"], ["🎁", "gifts presents birthday"],
  ["💐", "flowers bouquet florist"], ["🛋️", "furniture couch sofa home"], ["🛏️", "bed furniture bedroom hotel"],
  ["🪴", "plants garden houseplant"], ["🕯️", "candle decor home"],
  // transport
  ["🚗", "auto car vehicle driving"], ["🚙", "suv car auto vehicle"], ["🛻", "truck pickup auto vehicle"],
  ["🏍️", "motorcycle motorbike vehicle"], ["🛵", "scooter moped"], ["🚲", "bike bicycle cycling"],
  ["⛽", "gas fuel petrol gas station auto"], ["🔋", "battery charging electric ev"], ["🔌", "plug electric charging power"],
  ["🅿️", "parking"], ["🚕", "taxi cab rideshare uber lyft"], ["🚌", "bus transit public transport"],
  ["🚇", "subway metro transit train"], ["🚆", "train rail transit commute"], ["🚊", "tram streetcar transit"],
  ["✈️", "travel flights airfare airplane plane airline"], ["🛫", "flights departure travel airport"],
  ["🚢", "cruise ship boat travel"], ["⛵", "boat sailing"], ["🚧", "tolls road construction"], ["🛣️", "tolls highway road trip"],
  ["🔧", "repairs maintenance auto service wrench"], ["🛞", "tires wheel auto"], ["🚘", "car auto vehicle"],
  // travel and leisure
  ["🏨", "hotel lodging travel accommodation"], ["🏖️", "vacation beach holiday travel"], ["🏕️", "camping outdoors"],
  ["🗺️", "travel map trip"], ["🧳", "luggage travel suitcase"], ["🎢", "theme park amusement fun"], ["🎡", "fair amusement fun"],
  ["🎬", "movies cinema film entertainment"], ["🎭", "theater theatre shows arts entertainment"], ["🎟️", "tickets events concerts"],
  ["🎤", "concert karaoke music events"], ["🎵", "music"], ["🎧", "music audio headphones spotify"],
  ["📺", "tv television streaming cable"], ["🎮", "games gaming video games"], ["🎲", "games board games dice hobbies"],
  ["🎨", "art hobbies crafts painting"], ["🧶", "crafts knitting hobbies yarn"], ["📷", "camera photography hobbies"],
  ["🎣", "fishing hobbies outdoors"], ["⛳", "golf sports"], ["⚽", "sports soccer football"], ["🏀", "sports basketball"],
  ["🎾", "sports tennis"], ["⛷️", "skiing ski winter sports"], ["🏋️", "gym fitness workout exercise"],
  ["🧘", "yoga fitness wellness meditation"], ["🏊", "swimming pool fitness"], ["🎉", "party celebration events"],
  ["🎄", "christmas holidays"], ["🎃", "halloween holidays"], ["🎂", "birthday cake celebration"],
  // home and bills
  ["🏠", "home house housing rent mortgage"], ["🏡", "home house garden housing"], ["🏢", "office building rent apartment"],
  ["🔑", "rent keys lease housing"], ["💡", "utilities electricity electric power light bills"], ["⚡", "electricity power energy utilities"],
  ["🔥", "gas heating utilities fire"], ["💧", "water utilities"], ["🚰", "water utilities tap"], ["🗑️", "trash garbage waste utilities"],
  ["📱", "phone mobile cell"], ["☎️", "phone telephone landline"], ["📶", "internet wifi signal"], ["🌐", "internet web online"],
  ["💻", "computer laptop software tech electronics"], ["🖥️", "computer desktop electronics tech"], ["🖨️", "printer office supplies"],
  ["🔁", "subscriptions recurring"], ["🔄", "transfers recurring refresh"], ["🔨", "home improvement repairs tools hardware"],
  ["🛠️", "repairs maintenance tools hardware"], ["🧹", "cleaning housekeeping chores"], ["🧺", "laundry cleaning"],
  ["🧼", "soap cleaning household supplies"], ["🧻", "household supplies paper"], ["🌱", "garden gardening lawn plants"],
  ["🌳", "yard lawn landscaping trees"], ["🔒", "security alarm locks"], ["🛡️", "insurance protection security"],
  ["☂️", "insurance umbrella"], ["🏗️", "construction renovation remodel"], ["🚪", "door home"],
  // health
  ["🩺", "health medical doctor healthcare"], ["💊", "pharmacy medicine prescriptions drugs health"],
  ["🏥", "hospital medical health"], ["🦷", "dentist dental teeth"], ["👓", "glasses eyes vision optometrist"],
  ["🧠", "therapy mental health counseling"], ["❤️", "health heart love charity"], ["🩹", "first aid medical health"],
  ["💉", "vaccine shots medical"],
  // family and pets
  ["👶", "baby kids childcare"], ["🍼", "baby formula childcare kids"], ["🧸", "kids toys children"], ["🧒", "kids children"],
  ["👨‍👩‍👧", "family"], ["🏫", "school tuition education"], ["🎓", "education tuition college school student loans"],
  ["📚", "books education reading"], ["✏️", "school supplies stationery"], ["🐾", "pets pet care"], ["🐶", "dog pets"],
  ["🐱", "cat pets"], ["🐴", "horse pets"], ["🐟", "fish pets aquarium"], ["🦮", "dog pets walking"],
  // money
  ["💰", "money savings income cash"], ["💵", "cash money dollars bills"], ["💸", "spending money fees expenses"],
  ["💳", "credit card payment cards"], ["🏦", "bank banking fees loan mortgage"], ["🧾", "bills receipts taxes invoices"],
  ["📈", "investments stocks brokerage growth"], ["📉", "losses stocks"], ["💼", "work business job salary paycheck"],
  ["🪙", "coins crypto money"], ["🏛️", "government taxes"], ["⚖️", "legal lawyer fees"], ["🤝", "charity donations giving"],
  ["🙏", "charity donations church tithe"], ["⛪", "church religion tithe donations"], ["↩️", "refunds returns"],
  ["🚫", "excluded ignore none"], ["🏷️", "tag other misc uncategorized"], ["📊", "budget reports"], ["🧮", "accounting taxes"],
  ["✉️", "mail postage shipping"], ["📮", "postage mail shipping"], ["🖊️", "office supplies"], ["📎", "office supplies"],
  ["🗂️", "office business"], ["📰", "news newspaper subscriptions magazines"], ["🌍", "world travel international"],
];

/** The emoji whose words start with what's typed (every typed word must match one), in the list's order. */
export function searchEmoji(query: string): string[] {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return [];
  return EMOJI_WORDS.filter(([, names]) => {
    const own = names.split(" ");
    return words.every((w) => own.some((n) => n.startsWith(w)));
  }).map(([e]) => e);
}
