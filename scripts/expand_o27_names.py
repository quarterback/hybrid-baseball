"""
Expand the O27 name pool to reflect the league's launch-pathway demographics.

The O27 lore: the sport launches in East Asia, then explodes through
cricket-converted Commonwealth nations. By league year 5-10 the player
population is plausibly:

    North American   20-25%        Caribbean (Indo+Afro)   8-12%
    Latin American   15-20%        African (SA/Zim/Kenya)  5-8%
    South Asian      20-25%        British/Irish           3-5%
    East Asian       10-15%        Afghan/Central Asian    1-3%
    Australian/NZ    8-12%         Malaysian               2-4%

This script ADDS ethnolinguistic name buckets to the existing JSON files
(does not remove anything — old presets like `americas_pro` keep working).
The bucket keys are then referenced from regions.json which exposes the
new per-region distributions.

Run from repo root:
    python scripts/expand_o27_names.py

Idempotent — re-running silently overwrites the buckets it manages while
leaving every other bucket untouched.
"""
from __future__ import annotations
import json
import os
import sys

_NAMES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "o27v2", "data", "names",
)


# ===========================================================================
# MALE FIRST NAMES — new buckets
# ===========================================================================
MALE_FIRST_ADDITIONS: dict[str, list[str]] = {

    # -------- Indian regional first names --------
    "indian_north": [
        "Aarav", "Arjun", "Vivaan", "Aditya", "Vihaan", "Sai", "Reyansh",
        "Krishna", "Ishaan", "Shaurya", "Rohan", "Vikram", "Rajesh", "Suresh",
        "Amit", "Anil", "Ashwin", "Akash", "Ankit", "Ajay", "Anand", "Anant",
        "Anurag", "Arnav", "Atharv", "Bhavesh", "Chetan", "Dev", "Dhruv",
        "Dinesh", "Gaurav", "Harsh", "Hemant", "Himanshu", "Kabir", "Kunal",
        "Lakshay", "Manish", "Mohit", "Naveen", "Nikhil", "Nitin", "Prakash",
        "Pranav", "Prateek", "Pratham", "Raj", "Rakesh", "Ravi", "Rishabh",
        "Rishi", "Sachin", "Samar", "Sanjay", "Sandeep", "Shreyas", "Siddharth",
        "Sourav", "Sudhir", "Sumit", "Tarun", "Tushar", "Uday", "Varun",
        "Vinay", "Vishal", "Yash", "Yogesh", "Yuvraj", "Shubman", "Rishabh",
        "Shreyas", "Mayank", "Hardik", "Jasprit", "Mohammed", "Bhuvneshwar",
        "Cheteshwar", "Ishant", "KL",
    ],
    "indian_punjabi_sikh": [
        "Gurpreet", "Harpreet", "Manpreet", "Jagjit", "Harjit", "Sukhwinder",
        "Ranjit", "Surjit", "Harvinder", "Karanveer", "Inderjit", "Tarlochan",
        "Amrit", "Balwinder", "Charanjit", "Dilpreet", "Gurmeet", "Harbhajan",
        "Hardeep", "Iqbal", "Jaswant", "Kuldeep", "Mandeep", "Navjot",
        "Parminder", "Rajinder", "Sarabjit", "Satwinder", "Sukhdev",
        "Talwinder", "Yuvraj", "Arshdeep", "Shubman", "Navdeep",
    ],
    "indian_south": [
        # Tamil
        "Karthik", "Krishnan", "Murali", "Senthil", "Selvam", "Bharathi",
        "Surya", "Sundaram", "Mani", "Subramanian", "Venkat", "Chandran",
        "Kannan", "Arumugam", "Balaji", "Hariharan", "Dinesh", "Ravichandran",
        "Vijay", "Aravind", "Anirudh",
        # Telugu
        "Srinivas", "Venkata", "Ramana", "Mahesh", "Praveen", "Anjaneya",
        "Subba", "Pavan", "Phani", "Hari", "Nani", "Chiranjeevi", "Manohar",
        "Gopal", "Krishnamurthy", "Sridhar", "Naga", "Ramprasad", "Ambati",
        "Hanuma", "Tilak",
        # Kannada
        "Shivakumar", "Manjunath", "Basavaraj", "Mallikarjun", "Yashwant",
        "Adarsh", "Tejas", "Vasudev", "Anantha", "Raghuveer", "Devdutt",
        "Karun", "Vinay", "Robin",
        # Malayalam
        "Sanju", "Sachin", "Sreesanth", "Tinu", "Jomel", "Basil", "Vinod",
    ],
    "indian_west": [
        # Marathi
        "Vivek", "Ajit", "Ashish", "Atul", "Avinash", "Mangesh", "Mukul",
        "Nilesh", "Pradip", "Prashant", "Pravin", "Rajiv", "Santosh",
        "Shailesh", "Shrikant", "Sunil", "Suraj", "Vinod", "Rohit", "Ajinkya",
        "Shardul", "Kedar", "Ruturaj", "Prithvi",
        # Gujarati
        "Hardik", "Bhavin", "Chirag", "Darshan", "Hiren", "Jignesh", "Kishan",
        "Mihir", "Nirav", "Parth", "Pratik", "Mehul", "Maulik", "Falgun",
        "Sagar", "Bhargav", "Axar", "Cheteshwar", "Jasprit", "Munaf",
    ],
    "indian_east": [
        # Bengali Hindu
        "Anirban", "Arnab", "Bishal", "Debasish", "Goutam", "Indrajit",
        "Joydeep", "Kaushik", "Mainak", "Manas", "Pritam", "Rajdeep", "Rana",
        "Saurav", "Shubham", "Soumitra", "Subhash", "Sudip", "Sukanta",
        "Surajit", "Sushovan", "Swarup", "Tapan", "Uttam", "Avijit", "Bipul",
        "Chandan", "Debajyoti", "Dipankar", "Diptesh", "Hiranmoy", "Jayanta",
        "Niloy", "Ranjan", "Sayan", "Shamik", "Sourav", "Wriddhiman",
        "Mohammed", "Abhimanyu", "Riddhi",
    ],

    # -------- Pakistani --------
    "pakistani": [
        "Babar", "Hasan", "Ahmed", "Imran", "Wasim", "Waqar", "Shoaib",
        "Younis", "Misbah", "Inzamam", "Mohammad", "Asif", "Aamir", "Yasir",
        "Junaid", "Sarfaraz", "Azhar", "Fakhar", "Shaheen", "Naseem", "Haris",
        "Iftikhar", "Amjad", "Saeed", "Rashid", "Saqlain", "Shahid", "Tariq",
        "Umar", "Usman", "Faisal", "Bilal", "Saad", "Omar", "Salman", "Adeel",
        "Hamza", "Talha", "Zain", "Faheem", "Kamran", "Anwar", "Arshad",
        "Aleem", "Tahir", "Naveed", "Riaz", "Sohail", "Asad", "Faraz",
        "Furqan", "Idrees", "Jamshed", "Khurram", "Mansoor", "Mubashir",
        "Nadeem", "Owais", "Pervez", "Qasim", "Saud", "Tanveer", "Uzair",
        "Yasin", "Zeeshan", "Mohsin", "Iqbal", "Khalid", "Shadab", "Hussain",
        "Sajjad", "Azam",
    ],

    # -------- Bangladeshi --------
    "bangladeshi": [
        "Mushfiqur", "Tamim", "Mahmudullah", "Shakib", "Mustafizur", "Mehidy",
        "Liton", "Rubel", "Imrul", "Soumya", "Mosaddek", "Nasir", "Taskin",
        "Mahmud", "Rahim", "Faruk", "Khaled", "Mizan", "Nazmul", "Nasum",
        "Jubair", "Rakibul", "Saifuddin", "Sumon", "Tipu", "Anwar", "Ariful",
        "Asif", "Bashir", "Belal", "Habib", "Hasibul", "Hridoy", "Jahid",
        "Kamrul", "Mehedi", "Mominul", "Naeem", "Najmul", "Rejaul", "Saif",
        "Salahuddin", "Shadman", "Sharif", "Tanvir", "Yasir", "Zahid",
        "Aminul", "Aslam", "Mahbub", "Ebadot", "Towhid", "Litton",
        "Shoriful", "Mahedi",
    ],

    # -------- Sri Lankan --------
    "sri_lankan_sinhalese": [
        "Kumar", "Ranjan", "Sanath", "Kumara", "Mahela", "Lasith",
        "Tillakaratne", "Marvan", "Aravinda", "Muttiah", "Chaminda", "Nuwan",
        "Ajantha", "Dilshan", "Upul", "Thilan", "Kusal", "Dimuth", "Pathum",
        "Charith", "Wanindu", "Bhanuka", "Lahiru", "Dhananjaya", "Dasun",
        "Nimesh", "Maheesh", "Niroshan", "Suranga", "Akila", "Asela",
        "Chamika", "Dushmantha", "Isuru", "Kasun", "Lakshan", "Roshen",
        "Sachithra", "Thisara", "Avishka", "Kamindu", "Praveen", "Janith",
        "Asitha", "Binura", "Chanaka", "Dhanushka", "Dushan", "Janaka",
    ],
    "sri_lankan_tamil": [
        "Muralitharan", "Murali", "Sivanesan", "Suresh", "Ramanan", "Mahendran",
        "Selvaratnam", "Theekshana", "Pradeep", "Rangana", "Roshan", "Krishnan",
        "Sivakumar", "Thirimanne", "Thiruchelvam", "Viswanathan", "Yoganathan",
        "Sanjeev", "Mohan", "Dilan", "Niroshan", "Chandimal", "Vinothkumar",
        "Anand", "Arjun", "Ravi", "Rajesh", "Suresh", "Deepak",
    ],

    # -------- Indo-Caribbean (Trinidad / Guyana) --------
    "indo_caribbean": [
        "Shivnarine", "Sunil", "Ramnaresh", "Devendra", "Denesh", "Vishaul",
        "Tagenarine", "Veerasammy", "Sookchand", "Bhojnarine", "Bharath",
        "Lakshman", "Brijesh", "Ravindra", "Krishan", "Vishal", "Rajiv",
        "Anil", "Dinesh", "Naresh", "Suresh", "Ramesh", "Raj", "Roop",
        "Vivek", "Ashish", "Pravesh", "Hemraj", "Saheed", "Mahadeo", "Naresh",
        "Surajdeo", "Yannic", "Kavem", "Bhaskar", "Nikhil", "Daren", "Imran",
        "Khary", "Romario",
    ],
    "afro_caribbean": [
        "Akeem", "Andre", "Carlos", "Chris", "Devon", "Donovan", "Dwight",
        "Earl", "Ezekiel", "Gareth", "Jermaine", "Jerome", "Junior", "Kemar",
        "Kenroy", "Leon", "Marlon", "Marvin", "Nathaniel", "Nehemiah",
        "Oshane", "Othneil", "Preston", "Ramon", "Reggie", "Rohan", "Romain",
        "Roshan", "Roston", "Selwyn", "Shai", "Shamarh", "Shannon", "Sheldon",
        "Shimron", "Sulieman", "Tyrone", "Wayne", "Xavier", "Ackeem",
        "Brandon", "Jason", "Kraigg", "Nicholas", "Romario", "Sherfane",
        "Akeal", "Hayden", "Alzarri", "Obed", "Joshua", "Keacy", "Brandon",
        "Damarcus", "Chemar", "Yannic", "Yannick", "Odean", "Oraine",
        "Rovman", "Brian",
    ],

    # -------- Australia / NZ / Maori / Aboriginal --------
    "anglo_australian": [
        "Ashton", "Aaron", "Adam", "Allan", "Andrew", "Ben", "Brad", "Brendon",
        "Brett", "Cameron", "Cam", "Chris", "Damien", "Daniel", "Darren",
        "David", "Dean", "Doug", "Glenn", "Greg", "Harry", "Heath", "Hilton",
        "Jack", "James", "Jason", "Jeff", "Jesse", "Jhye", "Jhye", "Joel",
        "Josh", "Justin", "Lance", "Liam", "Marcus", "Marnus", "Mark",
        "Matthew", "Matt", "Michael", "Mitchell", "Mitch", "Moises", "Nathan",
        "Pat", "Patrick", "Paul", "Peter", "Rhys", "Ricky", "Sam", "Scott",
        "Sean", "Shane", "Shaun", "Simon", "Spencer", "Stephen", "Steve",
        "Stuart", "Tim", "Todd", "Travis", "Trent", "Wade", "Will",
        "Cameron", "Marnus", "Usman", "Mitchell", "Steve", "Glenn", "Aaron",
        "Travis", "Beau",
    ],
    "anglo_nz": [
        "Brendon", "Brent", "Brendan", "Cam", "Colin", "Daniel", "Daryl",
        "Dean", "Devon", "Doug", "Finn", "Glenn", "Grant", "Hamish", "Henry",
        "Ish", "Jeet", "Jacob", "James", "Jeetan", "Jimmy", "Joe", "Kane",
        "Kyle", "Lockie", "Mark", "Martin", "Matt", "Michael", "Mitchell",
        "Neil", "Nathan", "Neil", "Peter", "Rachin", "Ross", "Scott", "Shane",
        "Tim", "Todd", "Tom", "Trent", "Will", "Adam", "Blair", "Bruce",
        "Chris", "Devon", "Doug", "Grant", "Iain", "Kane", "Tom", "Will",
        "Finn", "Henry", "Glenn", "Andrew",
    ],
    "maori": [
        "Tamati", "Manaaki", "Wiremu", "Hemi", "Rawiri", "Tama", "Ariki",
        "Hone", "Hoani", "Ihaia", "Kahu", "Kane", "Manu", "Matua", "Mihaka",
        "Nikau", "Pita", "Rongo", "Ruru", "Tane", "Tipene", "Toa", "Whetu",
        "Hauraki", "Kingi", "Mateo", "Tipene", "Pirimia", "Tahu", "Manawa",
        "Ihaka", "Hira", "Marino", "Henare", "Kauri",
    ],
    "aboriginal_australian": [
        # First names of Aboriginal-Australian / Torres Strait origin used
        # in present-day Australia. Many indigenous Australian athletes use
        # English given names; this bucket captures the meaningful tail of
        # heritage-name usage that's growing in pro sport.
        "Bindi", "Kirra", "Jarli", "Jarrah", "Coen", "Bunji", "Yarran",
        "Wirra", "Tarni", "Karina", "Kaiya", "Daku", "Maliyan", "Marlu",
        "Naree", "Pirru", "Warragul", "Yowie", "Birrani", "Eden", "Jarrod",
        "Lowanna", "Mungo", "Nyngarra", "Tjurpin", "Jirra", "Yindi",
    ],

    # -------- South Africa --------
    "afrikaner": [
        "Hansie", "Faf", "Quinton", "Jacques", "Johan", "Pieter", "Hendrik",
        "Heinrich", "Marnus", "Aiden", "Wessel", "Werner", "Stefan", "Schalk",
        "Theunis", "Wian", "Wiaan", "Ryno", "Ruan", "Anrich", "Bjorn", "Conrad",
        "Cornelius", "Christian", "Daan", "Deon", "Dries", "Ernst", "Etienne",
        "Eugene", "Fanie", "Francois", "Fritz", "Gerhard", "Hannes", "Henk",
        "Hugo", "Janneman", "Jannie", "Jaco", "Jaap", "Joubert", "Kobus",
        "Lourens", "Lukas", "Marius", "Martin", "Morne", "Niel", "Nicolaas",
        "Pierre", "Rassie", "Rian", "Rudie", "Sarel", "Stiaan", "Tertius",
        "Theo", "Tinus", "Wessel", "Willem", "Wynand", "Bertus", "Dewald",
        "Andries", "Christiaan", "Reeza", "Tabraiz", "Lutho",
    ],
    "english_south_african": [
        "Graeme", "Kevin", "Mark", "Mike", "Allan", "Andrew", "Brett", "Bruce",
        "Dale", "Daniel", "David", "Gary", "Graham", "Hugh", "Ian", "Jonty",
        "Justin", "Kyle", "Lance", "Mark", "Neil", "Nicky", "Paul", "Robin",
        "Russell", "Shaun", "Stuart", "Wayne", "Anrich", "Beuran", "Glenton",
        "Lonwabo", "Marco", "Aaron", "Heinrich", "Reeza", "Tristan",
    ],
    "zulu": [
        "Sipho", "Sandile", "Themba", "Mfundo", "Lungi", "Lungile", "Thabang",
        "Thabo", "Thando", "Thulani", "Mandla", "Mbongiseni", "Mfanafuthi",
        "Sifiso", "Sibusiso", "Sithembiso", "Nkanyiso", "Nkosinathi", "Ntobeko",
        "Phila", "Phelelani", "Sanele", "Sazi", "Senzo", "Sibongiseni",
        "Siyabonga", "Wandile", "Welile", "Bongani", "Bongumusa", "Dumisani",
        "Khulekani", "Lindani", "Mduduzi", "Mthokozisi", "Musawenkosi",
        "Nhlanhla", "Sihle", "Sphesihle", "Thamsanqa", "Vusumuzi", "Zwelethu",
        "Sibu", "Khaya", "Senzwesihle",
    ],
    "xhosa": [
        "Lonwabo", "Loyiso", "Lungelo", "Lwazi", "Mzwandile", "Sive",
        "Siyanda", "Tembela", "Thando", "Thembekile", "Velile", "Vuyani",
        "Vuyo", "Vuyolwethu", "Xolani", "Yongama", "Zola", "Anele", "Andile",
        "Bandile", "Lutho", "Lwandile", "Mihlali", "Mlondolozi", "Monde",
        "Olwethu", "Sibulele", "Sihle", "Yola", "Zukile", "Khaya",
        "Buhlebethu", "Athenkosi", "Aviwe",
    ],
    "indian_south_african": [
        # Natal/SA Indian community — mostly Tamil / Telugu / Hindi heritage
        "Hashim", "Imraan", "Yusuf", "Junior", "Keshav", "Kreesen", "Krish",
        "Naveen", "Pravin", "Rohan", "Saud", "Vinay", "Vishaal", "Sashen",
        "Suren", "Suresh", "Tabraiz", "Reeza", "Premier", "Pravesh", "Senuran",
        "Karthik", "Hashim", "Imran", "Hardus",
    ],

    # -------- Zimbabwe --------
    "english_zimbabwean": [
        "Andy", "Brendan", "Brian", "Charles", "Chris", "Craig", "Daniel",
        "Dave", "Glenn", "Graeme", "Grant", "Henry", "Heath", "Ian", "John",
        "Kevin", "Kyle", "Mark", "Murray", "Neil", "Paul", "Sean", "Stuart",
        "Tom", "Sikandar", "Sean", "Heath", "Hamilton", "Gary",
    ],
    "shona": [
        "Tatenda", "Tinashe", "Munyaradzi", "Tendai", "Wesley", "Vusi",
        "Tafadzwa", "Kudzai", "Kudzanai", "Tawanda", "Tawana", "Takudzwa",
        "Tinotenda", "Kuda", "Tonderai", "Tendekai", "Farai", "Anesu",
        "Brighton", "Lovemore", "Nyasha", "Edmore", "Trevor", "Wellington",
        "Innocent", "Promise", "Honest", "Tendai", "Brendan", "Donald",
        "Hamilton", "Blessing", "Sean", "Ryan",
    ],
    "ndebele": [
        "Sithembile", "Sibonangaye", "Sikhumbuzo", "Themba", "Thabo",
        "Mthokozisi", "Khulani", "Bonginkosi", "Dumisani", "Nkosana",
        "Sibusiso", "Mthulisi", "Mkhululi", "Bekithemba", "Nkululeko",
        "Ntobeko", "Phathisa", "Vusumuzi", "Bhekani", "Mzilikazi", "Sibanda",
        "Nqobani", "Themba",
    ],

    # -------- Kenya --------
    "english_kenyan": [
        "Brendan", "Cameron", "Chris", "Damian", "Dave", "Hugh", "Hiren",
        "James", "Maurice", "Neil", "Peter", "Steve", "Tom", "Ravi", "Tony",
        "Adam", "Andrew", "Geoffrey", "Ian", "Mark", "Phil", "Roger", "Stuart",
        "Steve", "Brian", "Collins", "Thomas",
    ],
    "kikuyu": [
        "Kamau", "Mwangi", "Maina", "Macharia", "Karanja", "Kinuthia", "Kuria",
        "Mungai", "Munene", "Munyua", "Mathenge", "Mbugua", "Wachira",
        "Gakuru", "Ngugi", "Wahome", "Kimani", "Njoroge", "Kariuki", "Gichuru",
        "Mwaniki", "Murage", "Gichinga", "Muriithi", "Wanyama",
    ],
    "luo": [
        "Otieno", "Onyango", "Odhiambo", "Ouma", "Ochieng", "Owino", "Omondi",
        "Oduor", "Okoth", "Okello", "Okumu", "Obiero", "Onditi", "Opondo",
        "Owuor", "Ojwang", "Olum", "Otoyo", "Ojiambo", "Odera", "Ogada",
        "Ogalo",
    ],

    # -------- British / Irish --------
    "english_general": [
        "Harry", "Joe", "Jack", "James", "Tom", "Charlie", "Sam", "Ben",
        "Will", "Alex", "Matt", "Stuart", "Andrew", "Adam", "Nick", "Mike",
        "Chris", "Liam", "Owen", "Rory", "Eoin", "Jos", "Jonny", "Ollie",
        "Dom", "Dawid", "Mark", "Phil", "Rob", "Moeen", "Alastair", "Jofra",
        "Mark", "Jonathan", "Marcus", "Reece", "Zak", "Dan", "Olly", "Joshua",
        "Crispin", "George", "Henry", "Edward", "Theo", "Freddie", "Archie",
        "Toby", "Hugo", "Felix", "Oscar", "Louis", "Max", "Oliver",
    ],
    "scottish": [
        "Hamish", "Calum", "Callum", "Duncan", "Angus", "Ross", "Iain",
        "Cammy", "Alasdair", "Fergus", "Lachlan", "Logan", "Murray", "Niall",
        "Rory", "Ruairidh", "Stuart", "Andrew", "Bruce", "Cameron", "Connor",
        "David", "Donald", "Ewan", "Findlay", "Fraser", "Gavin", "Gordon",
        "Graham", "Greg", "Innes", "Jamie", "Kenny", "Lewis", "Liam", "Magnus",
        "Malcolm", "Neil", "Robert", "Ronnie", "Scott", "Sean", "Stewart",
        "Brodie",
    ],
    "welsh": [
        "Aled", "Bryn", "Cai", "Carwyn", "Dafydd", "Dai", "Dewi", "Dylan",
        "Eifion", "Emyr", "Evan", "Gareth", "Geraint", "Gethin", "Gruffydd",
        "Gwilym", "Hywel", "Iestyn", "Ieuan", "Iolo", "Lewis", "Llewelyn",
        "Llyr", "Meirion", "Morgan", "Owain", "Rhodri", "Rhys", "Sion",
        "Tomos", "Aled", "Bryn", "Cadog", "Caradog",
    ],
    "irish": [
        "Aidan", "Aiden", "Brendan", "Brian", "Cian", "Ciaran", "Colm",
        "Conor", "Cormac", "Daire", "Darragh", "Declan", "Diarmuid", "Donnacha",
        "Eamon", "Eoin", "Fergus", "Fionn", "Gearoid", "Liam", "Lorcan",
        "Niall", "Oisin", "Padraig", "Patrick", "Ronan", "Ruairi", "Seamus",
        "Sean", "Tadhg", "Conal", "Donal", "Daithi", "Aodhan", "Cathal",
        "Cillian", "Daragh", "Eoghan", "Killian", "Manus", "Tiernan",
        "Turlough", "Andrew", "Kevin", "Paul", "Mark",
    ],

    # -------- Afghanistan / Central Asia --------
    "pashto": [
        "Rashid", "Najibullah", "Hashmatullah", "Asghar", "Gulbadin", "Naveen",
        "Sediqullah", "Yamin", "Ihsanullah", "Rahmat", "Hazratullah", "Karim",
        "Riaz", "Mujeeb", "Fareed", "Noor", "Wafadar", "Khaibar", "Bilal",
        "Akbar", "Khairullah", "Zahid", "Yousaf", "Tauqir", "Sardar", "Sediq",
        "Shafiqullah", "Wasim", "Zahir", "Atif", "Haidar", "Saber", "Wahidullah",
        "Mohammad", "Najib", "Faridoon", "Asad", "Inayat", "Qais", "Samim",
        "Afsar", "Ehsanullah",
    ],
    "dari": [
        "Mansour", "Khalid", "Reza", "Ahmad", "Fakhruddin", "Najib",
        "Ahmadshah", "Hossein", "Faraidoon", "Farid", "Habib", "Mahmoud",
        "Khaled", "Hekmat", "Wahid", "Nasir", "Daud", "Naser", "Faisal",
        "Jamil", "Latif", "Nooruddin", "Sayed", "Shafiq", "Wali", "Yousuf",
        "Behzad", "Mohsen", "Massoud", "Ramin",
    ],
    "uzbek": [
        "Aziz", "Bakhtiyor", "Davron", "Diyor", "Doniyor", "Eldor", "Elyor",
        "Farrukh", "Furqat", "Iskandar", "Jakhongir", "Jasur", "Khurshid",
        "Mansur", "Murod", "Otabek", "Rasul", "Ruslan", "Rustam", "Sardor",
        "Shakhzod", "Shavkat", "Sherzod", "Sukhrob", "Timur", "Ulugbek",
        "Umid", "Yusuf", "Zafar", "Zokhid", "Bobur", "Akmal",
    ],

    # -------- Malaysia (the structural growth market) --------
    "malay": [
        # Malay Muslim names; "Mohd"/"Muhammad" prefix common, often with bin
        # patronymic — modeled here as standalone first names for the league.
        "Hafiz", "Hashim", "Razak", "Ismail", "Faris", "Aiman", "Hakim",
        "Iskandar", "Khairul", "Adam", "Aizat", "Amir", "Anwar", "Arif",
        "Azim", "Azlan", "Azwan", "Danial", "Faiz", "Fakhrul", "Farhan",
        "Hairul", "Haziq", "Imran", "Irfan", "Izzat", "Jamal", "Kamal",
        "Khalid", "Lutfi", "Mahathir", "Mahmud", "Mansor", "Mokhtar",
        "Mohd", "Muhammad", "Mustafa", "Nazri", "Norman", "Othman", "Rais",
        "Rashid", "Rizal", "Rizwan", "Roslan", "Sabri", "Saiful", "Salleh",
        "Shafiq", "Shahrul", "Shamsul", "Sharil", "Sufian", "Syed", "Taufik",
        "Wan", "Yusof", "Zaki", "Zubair", "Zulkifli",
    ],
    "chinese_malaysian": [
        # Romanized Chinese given names used by Chinese Malaysians (Hokkien
        # /Cantonese/Mandarin transliteration conventions). First-name
        # pool — surnames listed separately as they sit FIRST in formal use.
        "Chong", "Wei", "Boon", "Heng", "Chee", "Hock", "Keat", "Kheng",
        "Kim", "Lai", "Mun", "Pang", "Seng", "Soon", "Tat", "Teck", "Wai",
        "Wee", "Yew", "Yik", "Zheng", "Cheng", "Choon", "Eng", "Fai", "Hin",
        "Hsien", "Jian", "Kai", "Khye", "Kin", "Lim", "Loke", "Loon", "Ming",
        "Onn", "Pei", "Sheng", "Sun", "Vincent", "William", "Xian", "Yang",
        "Yong", "Zhi", "Wei Jian", "Boon Heong", "Chong Wei", "Wei Chong",
        "Yew Sin",
    ],
    "indian_malaysian": [
        # Tamil-Malaysian community names — overlap with south India but
        # pulled out so MY-specific roster mix can dial in independently.
        "Anand", "Arul", "Bala", "Chandran", "Devan", "Ganesh", "Gopal",
        "Hariharan", "Jaya", "Kannan", "Karthik", "Kumar", "Kumaran",
        "Lingam", "Mahesh", "Manogaran", "Mukesh", "Murugan", "Naga",
        "Naidu", "Nathan", "Prabakar", "Pranav", "Prem", "Rajesh", "Raju",
        "Ramesh", "Rao", "Rishi", "Sanjeev", "Selva", "Senthil", "Shankar",
        "Shyam", "Sivam", "Sudhakar", "Suresh", "Vasanth", "Vijay",
        "Vinod", "Vishnu",
    ],
}


# ===========================================================================
# FEMALE FIRST NAMES — new buckets (smaller; league default is male)
# ===========================================================================
FEMALE_FIRST_ADDITIONS: dict[str, list[str]] = {
    "indian_north": [
        "Aarohi", "Aaradhya", "Aanya", "Anika", "Ananya", "Aditi", "Avni",
        "Bhavna", "Charvi", "Diya", "Disha", "Divya", "Eesha", "Gauri",
        "Geetha", "Heena", "Ishita", "Jyoti", "Kavya", "Kiara", "Kriti",
        "Kavita", "Lakshmi", "Madhuri", "Mahi", "Manvi", "Meera", "Mira",
        "Mitali", "Naina", "Neha", "Nidhi", "Nisha", "Pooja", "Prachi",
        "Priya", "Pragya", "Radhika", "Rashmi", "Riya", "Sanya", "Shreya",
        "Shruti", "Swati", "Tanya", "Tara", "Vanya", "Vidhi", "Yashika",
    ],
    "indian_south": [
        "Aarthi", "Akshara", "Bhavani", "Deepika", "Divya", "Gayathri",
        "Hari", "Janani", "Kavitha", "Lakshmi", "Lalitha", "Mahalakshmi",
        "Malavika", "Malini", "Meenakshi", "Nandini", "Padmini", "Parvathi",
        "Ramya", "Revathi", "Saraswathi", "Saranya", "Shobana", "Sowmya",
        "Sushma", "Swathi", "Tara", "Uma", "Vasanthi", "Vidya",
    ],
    "indian_east": [
        "Anushka", "Aparna", "Bipasha", "Debjani", "Indira", "Indrani",
        "Jhumpa", "Kakoli", "Madhuri", "Mahasweta", "Manjari", "Mitali",
        "Moushumi", "Nandita", "Paromita", "Riya", "Sayantani", "Sharmila",
        "Shilpa", "Soumya", "Swastika", "Trishna", "Urmila", "Vidya",
    ],
    "indian_west": [
        "Aashka", "Avni", "Bhavna", "Disha", "Esha", "Falguni", "Hetal",
        "Jhanvi", "Khushi", "Komal", "Mansi", "Mira", "Nayna", "Niharika",
        "Pooja", "Priya", "Rachana", "Rina", "Shilpa", "Smruti", "Tanvi",
        "Vidhi", "Vidya", "Yamini",
    ],
    "pakistani": [
        "Aisha", "Ayesha", "Bisma", "Diana", "Fatima", "Hina", "Iram", "Javeria",
        "Kainat", "Khadija", "Maham", "Mariam", "Mehwish", "Nadia", "Nazia",
        "Nimra", "Noor", "Rabia", "Saima", "Sajida", "Samina", "Sana", "Sara",
        "Shaista", "Shazia", "Sidra", "Sumaira", "Umaima", "Yusra", "Zainab",
        "Zara", "Zoha", "Bismah", "Sana", "Aliya", "Diana", "Iqra", "Javeria",
        "Kainat", "Muneeba", "Nashra", "Sidra",
    ],
    "bangladeshi": [
        "Afsana", "Aisha", "Akhi", "Anika", "Anila", "Bipasha", "Farhana",
        "Farzana", "Fatima", "Habiba", "Halima", "Jannat", "Kaniz", "Khaleda",
        "Mahmuda", "Maliha", "Marina", "Mona", "Nasreen", "Nazma", "Nigar",
        "Nisha", "Nusrat", "Rubaiya", "Saima", "Salma", "Sharmin", "Shirin",
        "Sumi", "Tahmina", "Tania", "Tasnim", "Yasmin", "Zarin", "Salma",
        "Sharmin", "Sultana", "Rumana", "Nahida", "Murshida", "Lata", "Khadija",
    ],
    "sri_lankan_sinhalese": [
        "Achini", "Anushka", "Chamari", "Damayanthi", "Dilini", "Hashini",
        "Ishara", "Kalpani", "Kanchana", "Kushani", "Lakshmi", "Manuri",
        "Nilakshi", "Nirosha", "Oshadi", "Prasanna", "Prashangi", "Sandali",
        "Sanjeewani", "Shashikala", "Shirani", "Sumudu", "Surangika", "Tharindu",
        "Upeksha", "Vishmi", "Yashodha",
    ],
    "sri_lankan_tamil": [
        "Anitha", "Banuka", "Devika", "Geetha", "Indrani", "Jeyanthi",
        "Kavitha", "Lakshmi", "Malathi", "Nirmala", "Pavani", "Priya",
        "Radhika", "Sathiya", "Selvi", "Suganthi", "Sujatha", "Thanuja",
        "Uma", "Vasanthi",
    ],
    "indo_caribbean": [
        "Anjali", "Bhanmati", "Chandrika", "Devika", "Drupatie", "Indra",
        "Indrani", "Janki", "Kamla", "Kamini", "Lakshmi", "Mala", "Nalini",
        "Padma", "Parvati", "Rachna", "Rajwantie", "Reshma", "Roopwantie",
        "Sasha", "Savitri", "Shobha", "Sita", "Sunita", "Vani",
    ],
    "afro_caribbean": [
        "Akilah", "Aliyah", "Asha", "Brianna", "Camille", "Cherelle", "Danielle",
        "Deja", "Empress", "Imani", "Jamila", "Jasmine", "Kemika", "Keisha",
        "Latoya", "Marcia", "Marsha", "Nia", "Patrice", "Renee", "Sasha",
        "Shanice", "Shanika", "Stacey", "Tamara", "Tanesha", "Tiana", "Tiffany",
        "Toni", "Veronica", "Yvette", "Zora",
    ],
    "anglo_australian": [
        "Alyssa", "Annabel", "Beth", "Bonnie", "Brooke", "Caitlin", "Charlotte",
        "Chloe", "Courtney", "Ellyse", "Emma", "Erin", "Georgia", "Grace",
        "Hannah", "Heather", "Holly", "Imogen", "Indi", "Jess", "Karen",
        "Kate", "Kelly", "Laura", "Lauren", "Lucy", "Maddy", "Megan", "Meg",
        "Molly", "Nicole", "Olivia", "Phoebe", "Rachael", "Rebecca", "Rene",
        "Rhonda", "Ruth", "Sarah", "Scarlett", "Sophie", "Stephanie", "Tahlia",
        "Tayla", "Tess", "Zoe",
    ],
    "anglo_nz": [
        "Amelia", "Amy", "Bella", "Brooke", "Charlotte", "Claudia", "Eden",
        "Ella", "Emma", "Eve", "Georgia", "Greer", "Hannah", "Holly", "Isabel",
        "Izzy", "Jess", "Kate", "Katey", "Lea", "Maddy", "Maia", "Olivia",
        "Rosie", "Sophie", "Suzie",
    ],
    "maori": [
        "Aroha", "Anahera", "Atawhai", "Awhina", "Hana", "Hine", "Huia",
        "Kahurangi", "Kiri", "Mareikura", "Marama", "Maia", "Manaia", "Mihi",
        "Moana", "Nikau", "Pania", "Rawinia", "Tia", "Tipare", "Whetu",
        "Wikitoria",
    ],
    "afrikaner": [
        "Anika", "Anke", "Anneke", "Antoinette", "Carmen", "Christelle",
        "Dalene", "Elna", "Elsje", "Hanlie", "Ilse", "Janneke", "Karien",
        "Kim", "Lize", "Lourdes", "Lourise", "Marike", "Mariska", "Marizanne",
        "Marlene", "Marli", "Marizanne", "Nadine", "Nicole", "Pieta", "Riana",
        "Ronel", "Sune", "Suzaan", "Tania", "Tertia", "Wilna",
    ],
    "zulu": [
        "Andile", "Asanda", "Buhle", "Khanya", "Lindiwe", "Lungile", "Mbali",
        "Nandi", "Nokwanda", "Nomusa", "Nonhlanhla", "Nonkululeko", "Nosipho",
        "Ntombi", "Sindisiwe", "Sisanda", "Slindile", "Thandeka", "Thandi",
        "Thandiwe", "Thembi", "Zanele", "Zandile", "Zinhle", "Zodwa",
    ],
    "xhosa": [
        "Aviwe", "Babalwa", "Bandile", "Likhona", "Lindelwa", "Liyema",
        "Lwandile", "Lwazi", "Mihlali", "Mzansi", "Nolitha", "Nomsa",
        "Nondumiso", "Nontsikelelo", "Olwethu", "Phumzile", "Sibongile",
        "Siphokazi", "Sipho", "Tandiwe", "Vuyiswa", "Yolanda", "Zanele",
        "Zoleka",
    ],
    "english_kenyan": [
        "Abigail", "Anita", "Caroline", "Catherine", "Esther", "Faith",
        "Grace", "Hannah", "Jane", "Janet", "Joyce", "Karen", "Linda",
        "Margaret", "Mary", "Nancy", "Pamela", "Rachel", "Rebecca", "Sarah",
        "Susan",
    ],
    "kikuyu": [
        "Wanjiru", "Wangui", "Wairimu", "Wamuyu", "Gathoni", "Wambui",
        "Wangari", "Mukami", "Wangeci", "Mumbi", "Nyokabi", "Nyambura",
        "Wanjiku", "Wangechi", "Mwihaki", "Wachera",
    ],
    "luo": [
        "Achieng", "Akinyi", "Atieno", "Adongo", "Awour", "Awino", "Anyango",
        "Aoko", "Adoyo", "Apondi", "Awuor", "Aluoch", "Adhiambo",
    ],
    "english_general": [
        "Alice", "Amy", "Anna", "Beth", "Charlotte", "Chloe", "Claire",
        "Daisy", "Eleanor", "Ella", "Ellie", "Emily", "Emma", "Faye",
        "Grace", "Hannah", "Heather", "Holly", "Imogen", "Isabel", "Jane",
        "Jess", "Jessica", "Joanne", "Jodie", "Kate", "Katie", "Lauren",
        "Lily", "Lucy", "Megan", "Millie", "Molly", "Olivia", "Phoebe",
        "Rachel", "Rebecca", "Ruby", "Sophie", "Tanya", "Victoria", "Zoe",
    ],
    "scottish": [
        "Ailsa", "Aileen", "Alison", "Catriona", "Eilidh", "Fiona", "Iona",
        "Isla", "Kirsty", "Lorna", "Mairi", "Morag", "Rhona", "Shona",
        "Siobhan",
    ],
    "welsh": [
        "Alaw", "Angharad", "Arianwen", "Bethan", "Carys", "Catrin",
        "Cerys", "Eira", "Eirian", "Eluned", "Ffion", "Gwen", "Lowri",
        "Megan", "Meinir", "Nia", "Rhian", "Seren",
    ],
    "irish": [
        "Aine", "Aisling", "Aoife", "Bridget", "Caoimhe", "Ciara", "Dervla",
        "Eimear", "Fiona", "Grainne", "Maeve", "Niamh", "Orla", "Roisin",
        "Saoirse", "Sinead", "Siobhan",
    ],
    "pashto": [
        "Amina", "Bibi", "Fareeda", "Fatima", "Gulalai", "Habiba", "Hosna",
        "Khalida", "Laila", "Maryam", "Nargis", "Naseema", "Pari", "Saba",
        "Salma", "Sanga", "Shazia", "Yasmin", "Zarmina", "Zubaida",
    ],
    "dari": [
        "Anahita", "Aria", "Aryana", "Farishta", "Fatema", "Frozan", "Ghazal",
        "Khorshid", "Mahbouba", "Mariam", "Marwa", "Mehrnaz", "Nargess",
        "Roya", "Setareh", "Shabnam", "Shirin", "Soraya", "Yalda", "Zahra",
    ],
    "uzbek": [
        "Aida", "Dilfuza", "Dilnoza", "Feruza", "Gulnara", "Iroda",
        "Kamola", "Lola", "Madina", "Malika", "Nargiza", "Nilufar", "Nodira",
        "Sayora", "Sevara", "Shahnoza", "Umida", "Yulduz", "Zarina",
    ],
    "malay": [
        "Aida", "Aishah", "Alia", "Aminah", "Ayu", "Azlina", "Azura",
        "Diana", "Fadhilah", "Faradila", "Faridah", "Fariza", "Hafizah",
        "Hajar", "Halimah", "Hanani", "Hanim", "Hasnah", "Hidayah", "Husna",
        "Ida", "Intan", "Izyan", "Jamilah", "Khadijah", "Khairunnisa",
        "Latifah", "Maizan", "Mariam", "Maslinda", "Maziah", "Mira",
        "Munirah", "Nadia", "Najwa", "Naqibah", "Nasuha", "Nazreen", "Nik",
        "Noraini", "Norazlin", "Norhayati", "Norliza", "Norsuhana", "Nur",
        "Nurin", "Puteri", "Rabiatul", "Rahmah", "Raihana", "Roslina",
        "Rosmah", "Sakinah", "Salina", "Salmah", "Shahidah", "Sharifah",
        "Siti", "Sumaiyyah", "Suraya", "Syafiqah", "Wahidah", "Yasmin",
        "Yusra", "Zarina", "Zulaikha", "Zuriana",
    ],
    "chinese_malaysian": [
        "Bee", "Chui", "Chien", "Choon", "Hui", "Hwa", "Jia", "Jia Yi",
        "Kim", "Lan", "Li", "Mei", "Min", "Ming", "Pei", "Sue", "Suet",
        "Sze", "Tian", "Wai", "Wei", "Wen", "Xiu", "Xin", "Yan", "Yi", "Yin",
        "Yong", "Yu", "Yuen",
    ],
    "indian_malaysian": [
        "Anitha", "Bhavani", "Devi", "Geetha", "Indra", "Jaya", "Kala",
        "Kamala", "Kavitha", "Lakshmi", "Lalitha", "Latha", "Malar", "Mala",
        "Meena", "Nalini", "Pavithra", "Priya", "Radha", "Rani", "Saraswathi",
        "Selvi", "Shanthi", "Sumathi", "Uma", "Vani", "Vasanthi", "Vidhya",
    ],
}


# ===========================================================================
# SURNAMES — new buckets
# ===========================================================================
SURNAME_ADDITIONS: dict[str, list[str]] = {

    # -------- Indian regional surnames --------
    "indian_north": [
        "Sharma", "Verma", "Gupta", "Agarwal", "Mishra", "Tiwari", "Pandey",
        "Yadav", "Kumar", "Singh", "Chauhan", "Bhardwaj", "Tripathi",
        "Srivastava", "Kohli", "Shukla", "Saxena", "Mehra", "Khanna",
        "Bhatia", "Sehgal", "Mittal", "Goel", "Aggarwal", "Sinha", "Rastogi",
        "Tandon", "Vohra", "Bansal", "Jaiswal", "Nigam", "Dwivedi", "Joshi",
        "Bhandari", "Rajput", "Thakur", "Chaudhary",
    ],
    "indian_punjabi_sikh": [
        "Singh", "Kaur", "Sandhu", "Sidhu", "Dhaliwal", "Gill", "Mann",
        "Brar", "Cheema", "Dhillon", "Khaira", "Sodhi", "Sahota", "Sangha",
        "Atwal", "Bains", "Bhullar", "Garcha", "Grewal", "Hothi", "Kahlon",
        "Khangura", "Lehal", "Pannu", "Randhawa", "Sangha", "Toor", "Virk",
        "Aulakh", "Bedi", "Chahal",
    ],
    "indian_south_tamil": [
        "Iyer", "Iyengar", "Krishnan", "Subramanian", "Sundaram", "Chandran",
        "Ramachandran", "Venkatesan", "Ramanathan", "Ramaswamy",
        "Balasubramanian", "Mahadevan", "Natarajan", "Ganesan", "Rajan",
        "Pillai", "Mani", "Sivaraman", "Murugan", "Karthikeyan", "Senthil",
        "Saravanan", "Selvam", "Sundar", "Anand",
    ],
    "indian_south_telugu": [
        "Reddy", "Naidu", "Rao", "Raju", "Prasad", "Chowdary", "Sharma",
        "Sastry", "Sarma", "Murthy", "Sai", "Kumar", "Vamsi", "Lakshman",
        "Goud", "Yadav", "Verma", "Ananth", "Ranga", "Subba", "Venkata",
    ],
    "indian_south_kannada": [
        "Gowda", "Hegde", "Shetty", "Bhat", "Rao", "Kulkarni", "Kamath",
        "Pai", "Murthy", "Acharya", "Nayak", "Rao", "Karkala", "Adiga",
        "Hiremath",
    ],
    "indian_west_marathi": [
        "Patil", "Joshi", "Deshmukh", "Kulkarni", "Bhosale", "Jadhav",
        "More", "Gaikwad", "Pawar", "Shinde", "Sawant", "Kale", "Sharma",
        "Pandit", "Bhonsale", "Marathe", "Phadke", "Khot", "Chavan",
        "Salunkhe",
    ],
    "indian_west_gujarati": [
        "Patel", "Shah", "Mehta", "Desai", "Modi", "Trivedi", "Joshi",
        "Vyas", "Pandya", "Bhatt", "Dave", "Acharya", "Soni", "Parikh",
        "Thakkar", "Doshi", "Gandhi", "Kotak", "Choksi", "Vora",
    ],
    "indian_east": [
        # Bengali Hindu
        "Banerjee", "Mukherjee", "Chatterjee", "Bhattacharya", "Das", "Ghosh",
        "Sen", "Roy", "Dutta", "Bose", "Sarkar", "Chakraborty", "Chowdhury",
        "Saha", "Mitra", "Pal", "Basu", "Majumdar", "Bagchi", "Ganguly",
        "Tagore", "Mondal", "Maity", "Adhikari",
    ],

    # -------- Pakistani --------
    "pakistani": [
        "Khan", "Ahmed", "Iqbal", "Hussain", "Ali", "Sheikh", "Akhtar",
        "Anwar", "Aslam", "Aziz", "Babar", "Bashir", "Bhatti", "Butt",
        "Chaudhry", "Cheema", "Chishti", "Dar", "Faisal", "Farooq", "Ghani",
        "Hashmi", "Iftikhar", "Imran", "Jamil", "Javed", "Khalid", "Latif",
        "Mahmood", "Malik", "Mansoor", "Mirza", "Munir", "Naqvi", "Nazir",
        "Niazi", "Qadir", "Qureshi", "Raja", "Rashid", "Rauf", "Rehman",
        "Saeed", "Saifi", "Shafiq", "Shah", "Siddiqui", "Tariq", "Yousaf",
        "Zafar", "Awan", "Janjua", "Mughal", "Tanveer",
    ],

    # -------- Bangladeshi --------
    "bangladeshi": [
        "Rahman", "Hossain", "Islam", "Khan", "Ahmed", "Ahmad", "Hasan",
        "Hoque", "Akter", "Begum", "Sarker", "Mia", "Miah", "Mondal",
        "Bhuiyan", "Talukder", "Chowdhury", "Sheikh", "Karim", "Kabir",
        "Mahmud", "Alam", "Roy", "Das", "Chakma", "Tripura", "Hasin", "Sultan",
        "Sumon", "Faruk",
    ],

    # -------- Sri Lankan --------
    "sri_lankan_sinhalese": [
        "Perera", "Silva", "Fernando", "Jayawardena", "Wickramasinghe",
        "Karunaratne", "Mendis", "Bandara", "Dissanayake", "Senanayake",
        "Wickramaratne", "Tillakaratne", "Jayasuriya", "Vaas", "Dilshan",
        "Mathews", "Karunatilaka", "Senaratne", "Ranatunga", "Pushpakumara",
        "Gunaratne", "Liyanage", "Pathirana", "Samaraweera", "Senerath",
        "Hasaranga", "Asalanka", "Rajapaksa",
    ],
    "sri_lankan_tamil": [
        "Mahendran", "Thiruchelvam", "Sivakumar", "Selvaratnam", "Kanagaratnam",
        "Sangakkara", "Murugesu", "Viswanathan", "Yogeswaran", "Ramanathan",
        "Theekshana", "Krishnan", "Selvanayagam", "Subramaniam", "Yoganathan",
    ],

    # -------- Indo-Caribbean --------
    "indo_caribbean": [
        "Persaud", "Singh", "Maharaj", "Ramnarine", "Rambharose", "Persad",
        "Ramcharan", "Ramdial", "Ramdin", "Ramdass", "Ramcharitar", "Ramnarayan",
        "Sookoo", "Sookdeo", "Sookram", "Ganga", "Ramphal", "Ramessar",
        "Bhola", "Mahadeo", "Sarwan", "Chanderpaul", "Bishoo", "Chattergoon",
        "Bhagwan", "Hanoman", "Hosein", "Mangru", "Naipaul", "Rampaul",
        "Ramsumair", "Tewarie", "Persaud",
    ],
    "afro_caribbean": [
        "Lara", "Gayle", "Holding", "Ambrose", "Walsh", "Marshall", "Greenidge",
        "Roach", "Brathwaite", "Hetmyer", "Hope", "Bishop", "Garner",
        "Russell", "Pollard", "Bravo", "Sammy", "Powell", "Lewis", "Cottrell",
        "Holder", "Chase", "Pooran", "Shepherd", "Rutherford", "Walsh",
        "King", "Blackwood", "Drakes", "Joseph", "Smith", "Williams", "Charles",
        "Christopher", "Daniels", "Edwards", "Francis", "Henry", "Jackson",
        "James", "Johnson", "Phillips",
    ],

    # -------- Australia / NZ --------
    "anglo_australian": [
        "Smith", "Johnson", "Brown", "Miller", "Wilson", "Anderson", "Taylor",
        "Thompson", "Jones", "Williams", "White", "Harris", "Martin",
        "Thompson", "Garcia", "Robinson", "Clark", "Rodriguez", "Lewis",
        "Walker", "Hall", "Allen", "Young", "Hernandez", "King", "Wright",
        "Lopez", "Hill", "Scott", "Green", "Adams", "Baker", "Gonzalez",
        "Nelson", "Carter", "Mitchell", "Roberts", "Turner", "Phillips",
        "Campbell", "Parker", "Evans", "Edwards", "Collins", "Stewart",
        "Sanchez", "Morris", "Rogers", "Reed", "Cook", "Morgan", "Bell",
        "Murphy", "Bailey", "Rivera", "Cooper", "Richardson", "Cox", "Howard",
        "Ward", "Cooke", "Clarke", "Marsh", "Cummins", "Starc", "Lyon",
        "Maxwell", "Stoinis", "Carey", "Head", "Inglis", "Hazlewood",
        "Khawaja", "Labuschagne",
    ],
    "anglo_nz": [
        "Williamson", "Southee", "Boult", "Latham", "Conway", "Ravindra",
        "Henry", "Jamieson", "Young", "Mitchell", "Phillips", "Sodhi",
        "Sears", "Bracewell", "Bracewell", "Astle", "McCullum", "Vettori",
        "Cairns", "Crowe", "Hadlee", "Greatbatch", "Marshall", "Patel",
        "Sinclair", "Smith", "Taylor", "Watling", "Wagner", "Anderson",
        "Allen", "Blundell", "Chapman", "Duffy",
    ],
    "maori": [
        "Te Aroha", "Te Whatu", "Ngata", "Te Kanawa", "Wiremu", "Hemi",
        "Tipene", "Heke", "Hiroti", "Hokianga", "Kahukura", "Kingi",
        "Mahuta", "Manaia", "Manu", "Marsden", "Matua", "Pomare", "Puhi",
        "Rangi", "Rangihau", "Rongo", "Tahi", "Tane", "Wahine", "Waka",
        "Whareaitu",
    ],

    # -------- South Africa --------
    "afrikaner": [
        "van der Merwe", "Botha", "Steyn", "du Plessis", "de Villiers",
        "Pretorius", "Coetzee", "Bothma", "Janse van Rensburg", "Theron",
        "Nel", "Smit", "Marais", "Joubert", "Greyling", "van Wyk", "van Zyl",
        "Erasmus", "Visser", "Olivier", "Roux", "Strydom", "van der Walt",
        "du Toit", "Engelbrecht", "Botha", "Conradie", "Van Heerden", "Kruger",
        "Maritz", "Meyer", "Pretorius", "Snyman", "Swart", "Venter", "Vermeulen",
        "Vorster", "Wessels", "Bezuidenhout", "Markram", "Verreynne", "Linde",
        "Burger", "de Kock", "du Plessis", "van der Dussen",
    ],
    "english_south_african": [
        "Smith", "Anderson", "Cooper", "Brown", "Wilson", "Taylor", "Hill",
        "Stewart", "Walker", "Cook", "Green", "Marsh", "Robinson", "Davis",
        "Mitchell", "Jordan", "King", "Boucher", "Pollock", "Rhodes",
        "Cronje", "Gibbs", "Kallis", "Klusener", "Boje", "Lonwabo", "Stephens",
    ],
    "zulu": [
        "Ndlovu", "Khumalo", "Zulu", "Dlamini", "Sibiya", "Mhlongo",
        "Buthelezi", "Mthembu", "Zungu", "Mkhize", "Hadebe", "Cele",
        "Mabaso", "Madlala", "Mthethwa", "Nkosi", "Nzama", "Shozi", "Vilakazi",
        "Zwane", "Mzobe", "Ngcobo", "Hlongwane", "Mahlangu", "Maphumulo",
        "Mdletshe", "Mkhwanazi", "Mtshali", "Ntuli", "Sibanyoni", "Tshabalala",
        "Twala", "Xulu",
    ],
    "xhosa": [
        "Mandela", "Mbeki", "Mfeka", "Ngcobo", "Mqikela", "Sobukwe", "Tutu",
        "Hani", "Tambo", "Zondi", "Zulu", "Bavuma", "Maharaj", "Mthembu",
        "Maqala", "Mhambi", "Mhlawuli", "Mphahlele", "Ngculu", "Nzimande",
        "Sithole", "Skosana", "Tinta", "Yili", "Zwelibanzi", "Mzwandile",
    ],
    "indian_south_african": [
        "Naidoo", "Pillay", "Govender", "Reddy", "Padayachee", "Naicker",
        "Moodley", "Singh", "Maharaj", "Chetty", "Sookdeo", "Shaik", "Patel",
        "Khan", "Bhana", "Kara", "Nair", "Rajab", "Sayed", "Sheik", "Vahed",
        "Abrahams", "Behardien", "Phangiso", "Tahir",
    ],

    # -------- Zimbabwe --------
    "english_zimbabwean": [
        "Taylor", "Williams", "Streak", "Flower", "Heath", "Rogers", "Houghton",
        "Goodwin", "Strang", "Whittall", "Olonga", "Brent", "Friend",
        "Trench", "Erwee", "Burl", "Williams", "Erwee",
    ],
    "shona": [
        "Mugabe", "Tsvangirai", "Murenga", "Chinotimba", "Madondo", "Chinhoyi",
        "Chigumbu", "Chinyemba", "Dube", "Manyika", "Marufu", "Masamvu",
        "Masvingo", "Mazarura", "Mhlanga", "Moyo", "Mpofu", "Mudimu",
        "Mudzongo", "Munyai", "Mutsvangwa", "Mwanza", "Ndlovu", "Ngara",
        "Nyamhunga", "Sibanda", "Tshuma", "Zinyemba", "Madhevere", "Marumani",
        "Masakadza", "Maruma", "Tafara", "Raza",
    ],
    "ndebele": [
        "Ncube", "Sibanda", "Moyo", "Nkomo", "Dube", "Ndlovu", "Khumalo",
        "Mathema", "Mathuthu", "Mhlanga", "Ngwenya", "Nyathi", "Tshuma",
        "Mpofu", "Mthembu",
    ],

    # -------- Kenya --------
    "kikuyu": [
        "Kamau", "Kariuki", "Mwangi", "Njoroge", "Kinuthia", "Macharia",
        "Karanja", "Mungai", "Wachira", "Mathenge", "Mbugua", "Ngugi",
        "Wahome", "Kimani", "Gichuru", "Mwaniki", "Murage", "Maina",
        "Munyua", "Munene", "Kuria", "Wanjiru", "Wangui", "Wambui",
    ],
    "luo": [
        "Otieno", "Onyango", "Odhiambo", "Ouma", "Ochieng", "Owino",
        "Omondi", "Oduor", "Okoth", "Okello", "Okumu", "Obiero", "Onditi",
        "Opondo", "Owuor", "Ojwang", "Olum", "Otoyo", "Ojiambo", "Odera",
        "Ogada", "Ogalo",
    ],
    "english_kenyan": [
        "Patel", "Shah", "Hirji", "Chudasama", "Karim", "Mehta", "Khan",
        "Khimji", "Madhvani", "Lakhani", "Otieno", "Tikolo", "Obuya",
        "Suji", "Odoyo", "Modi", "Patel", "Mishra",
    ],

    # -------- British / Irish --------
    "english_general": [
        "Smith", "Jones", "Williams", "Brown", "Taylor", "Davies", "Wilson",
        "Evans", "Thomas", "Roberts", "Johnson", "Lewis", "Walker", "Robinson",
        "Wood", "Thompson", "White", "Watson", "Jackson", "Wright", "Green",
        "Harris", "Cooper", "King", "Lee", "Martin", "Clarke", "James",
        "Morgan", "Hughes", "Edwards", "Hill", "Moore", "Clark", "Harrison",
        "Scott", "Young", "Morris", "Hall", "Ward", "Turner", "Carter",
        "Phillips", "Mitchell", "Patel", "Adams", "Campbell", "Anderson",
        "Allen", "Cook", "Stokes", "Root", "Anderson", "Bairstow", "Brook",
        "Buttler", "Curran", "Foakes", "Bairstow", "Wood", "Crawley",
        "Pope", "Robinson", "Atkinson", "Rashid", "Moeen",
    ],
    "scottish": [
        "MacDonald", "Campbell", "Stewart", "Wilson", "Robertson", "Mackay",
        "Mackenzie", "MacLeod", "Murray", "Ross", "Cameron", "Fraser",
        "Henderson", "Hunter", "Kerr", "MacIntosh", "MacKinnon", "MacMillan",
        "MacRae", "Maclean", "Morrison", "Morton", "Munro", "Reid", "Rennie",
        "Sutherland", "Watt", "Hamilton", "Niven", "Coetzer", "Cross",
        "Berrington",
    ],
    "welsh": [
        "Jones", "Williams", "Davies", "Evans", "Thomas", "Lewis", "Roberts",
        "Hughes", "Morgan", "Griffiths", "Edwards", "Owen", "Phillips",
        "Powell", "Price", "Pritchard", "Pugh", "Rees", "Vaughan", "Wynn",
        "Bowen", "Howell", "James", "Llewellyn",
    ],
    "irish": [
        "Murphy", "Kelly", "O'Sullivan", "Walsh", "Smith", "O'Brien", "Byrne",
        "Ryan", "O'Connor", "O'Neill", "O'Reilly", "Doyle", "McCarthy",
        "Gallagher", "O'Doherty", "Kennedy", "Lynch", "Murray", "Quinn",
        "Moore", "McLoughlin", "O'Carroll", "Connolly", "Daly", "Wilson",
        "Dunne", "Brennan", "Burke", "Collins", "Campbell", "Clarke", "Johnston",
        "Hughes", "Farrell", "Fitzgerald", "Brown", "Martin", "Maguire",
        "Nolan", "Flynn", "Thompson", "Callaghan", "O'Donnell", "Duffy",
        "Mahony", "Boyd", "Mooney", "Stirling", "Balbirnie", "Tucker",
        "Tector", "Adair",
    ],

    # -------- Afghanistan / Central Asia --------
    "pashto": [
        "Khan", "Afridi", "Yousafzai", "Mohmand", "Wazir", "Mahsud",
        "Orakzai", "Bangash", "Khattak", "Durrani", "Niazi", "Achakzai",
        "Stanikzai", "Zazai", "Zadran", "Mangal", "Janat", "Kakar",
        "Ahmadzai", "Ahmadi", "Naib", "Atal", "Shahidi", "Shah", "Haq",
        "Rahman", "Hassan", "Karim", "Janat", "Ibrahimi",
    ],
    "dari": [
        "Hashemi", "Mohammadi", "Rahimi", "Hossaini", "Ahmadi", "Karimi",
        "Mansouri", "Nazari", "Rezaei", "Sadat", "Yaqubi", "Akbari",
        "Alavi", "Amini", "Ansari", "Asadi", "Azizi", "Bashiri", "Daoudi",
        "Faizi", "Faqiri", "Ghafoori", "Habibi", "Jalili", "Kabuli",
        "Khalili", "Latifi", "Mosavi", "Naderi", "Omari", "Qadiri", "Sharifi",
        "Yusufi", "Zahir",
    ],
    "uzbek": [
        "Karimov", "Yusupov", "Rashidov", "Akhmedov", "Aliyev", "Azimov",
        "Bakhtiyor", "Berdiyev", "Davlatov", "Ergashev", "Faizullaev",
        "Gulomov", "Hamidov", "Iskandarov", "Jalilov", "Kamalov", "Latipov",
        "Madumarov", "Mansurov", "Nasriddinov", "Otaev", "Pulatov", "Qodirov",
        "Rasulov", "Saidov", "Tashkentov", "Umarov", "Yuldashev", "Zokirov",
    ],

    # -------- Malaysia --------
    "malay": [
        # Malay Muslim "surnames" — most Malays use a patronymic (X bin Y),
        # so the "surname" position holds the father's name. We model that
        # as a stable family-name slot with common Muslim/Malay names.
        "bin Abdullah", "bin Ahmad", "bin Hassan", "bin Hussein", "bin Ismail",
        "bin Mohammad", "bin Mustafa", "bin Razak", "bin Salleh", "bin Yusof",
        "Abdullah", "Ahmad", "Ali", "Aziz", "Bakar", "Daud", "Hamid", "Hashim",
        "Hassan", "Ibrahim", "Idris", "Ismail", "Jaafar", "Kamal", "Karim",
        "Khairi", "Khalid", "Lee", "Mahmud", "Majid", "Mansur", "Mohamad",
        "Mohd", "Musa", "Mustafa", "Nasir", "Omar", "Othman", "Rahman",
        "Rashid", "Razak", "Salleh", "Samad", "Sani", "Shafie", "Shah",
        "Sulaiman", "Syed", "Tahir", "Wahab", "Yahya", "Yaacob", "Yusof",
        "Zainal",
    ],
    "chinese_malaysian": [
        # Chinese Malaysian family names — ranked roughly by community
        # frequency. Hokkien / Cantonese / Mandarin surface forms.
        "Lim", "Tan", "Lee", "Ng", "Wong", "Chong", "Chan", "Chua", "Goh",
        "Ho", "Khoo", "Ong", "Yeo", "Yap", "Teh", "Sim", "Lau", "Loh",
        "Foo", "Phang", "Toh", "Kok", "Cheng", "Cheong", "Liew", "Loke",
        "Mak", "Pang", "Quek", "Seah", "See", "Tay", "Teoh", "Woo", "Yong",
        "Soon", "Beh", "Boon", "Chai", "Heng", "Lai", "Leong", "Low", "Oh",
        "Pung", "Saw", "Sin", "Soh", "Soo", "Tee", "Wee", "Yee", "Yeoh",
        "Yong",
    ],
    "indian_malaysian": [
        # Tamil-Malaysian community surnames.
        "Subramaniam", "Murugan", "Krishnan", "Nair", "Pillai", "Rajagopal",
        "Selvaraj", "Sivanesan", "Chandran", "Devan", "Ganesan", "Govindasamy",
        "Maniam", "Muthusamy", "Nadarajah", "Naidu", "Palanisamy", "Pandian",
        "Perumal", "Raju", "Ramachandran", "Ramasamy", "Saravanan",
        "Selvanathan", "Sundaram", "Thangavelu", "Veerasamy", "Vijayan",
        "Ariff", "Marimuthu", "Sandakan", "Nagarajan",
    ],
}


# ===========================================================================
# RUNNER
# ===========================================================================

def _patch(path: str, additions: dict[str, list[str]]) -> tuple[int, int]:
    """Merge `additions` into the JSON file at `path`. Existing keys are
    overwritten with the new lists (so re-running this script gives the
    canonical pool); other existing keys are left untouched. Returns
    (n_keys_total, n_keys_added_or_updated)."""
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    n_changed = 0
    for k, v in additions.items():
        # de-dupe while preserving order
        seen: set[str] = set()
        cleaned: list[str] = []
        for n in v:
            if n not in seen:
                seen.add(n)
                cleaned.append(n)
        if data.get(k) != cleaned:
            n_changed += 1
        data[k] = cleaned
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    return len(data), n_changed


def main() -> None:
    targets = [
        ("male_first.json",   MALE_FIRST_ADDITIONS),
        ("female_first.json", FEMALE_FIRST_ADDITIONS),
        ("surnames.json",     SURNAME_ADDITIONS),
    ]
    for fname, additions in targets:
        path = os.path.join(_NAMES_DIR, fname)
        total, changed = _patch(path, additions)
        print(f"{fname}: {changed} keys added/updated, {total} total buckets")


if __name__ == "__main__":
    sys.exit(main())
