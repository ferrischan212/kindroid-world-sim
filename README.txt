LORA SCENE APP
==============

WHAT IS THIS?
-------------
A small Windows app that gives a Kindroid AI (here called "Lora", with her dog "Mochi")
a living world. On a timer, it changes what she is doing and where she is - reading a
book, watering the garden, sitting on the porch - and a "Narrator" tells her about it
in the chat. Before each change, the Narrator asks her if she wants something else.
If she asks for something, the Narrator does exactly what she asked.

Think of it like this:
  - Kindroid is Lora's brain (she talks and has a personality).
  - DeepSeek is the storyteller (it decides what happens next when she doesn't).
  - This app is the clock and the stage crew (it keeps time and changes the scene).


WHAT YOU NEED
-------------
1. A Windows computer.
2. Python 3.11 or newer (free, from python.org). When installing, tick
   "Add Python to PATH".
3. A Kindroid account with API access, and the AI you want to use.
4. A DeepSeek account and API key (platform.deepseek.com). It costs a few cents a day.
5. Three "profiles" (personas) in Kindroid for that AI: Master (you), Narrator,
   and Mochi. The app switches between them so the Narrator can post messages.


SETUP (ONE TIME)
----------------
1. Put this folder anywhere you like.

2. Install the one extra piece it needs. Open a command window in this folder and type:
       pip install -r requirements.txt

3. Add your keys:
   - Make a copy of ".env.example" and name the copy ".env" (just ".env", nothing before the dot).
   - Open ".env" in Notepad and fill in:
       DEEPSEEK_API_KEY=   your DeepSeek key
       KINDROID_API_KEY=   your Kindroid API key
       KINDROID_AI_ID=     your Kindroid AI's ID
     (Kindroid: Settings -> General -> API & advanced integrations.)
   - NEVER share your .env file. It holds your keys.

4. Add your profile IDs:
   Each of the three profiles you chat as (Master, Narrator, Mochi) has a hidden ID.
   Kindroid doesn't show it anywhere in the app, so you copy it from your web browser
   while switching profiles. Do this on a computer, at kindroid.ai, in Chrome or Edge.

   a) Open kindroid.ai and go to the chat with your AI.
   b) Press F12. A panel opens (the "developer tools").
   c) In that panel, click the "Network" tab.
   d) In the small "Filter" box in that panel, type:  update-info
   e) Now, in Kindroid, switch to the Master profile (the same way you normally
      change who you are chatting as).
   f) A line called "update-info" appears in the panel. Click it.
   g) Click the "Payload" tab next to it. You will see something like:
          active_persona_id: "AbC123xYz..."
      That long code is the Master profile's ID. Copy it (without the quotes).
   h) Open "config.json" in Notepad and paste it between the quotes after "id"
      under master_profile.
   i) Do e) to h) again for the Narrator profile (narrator_profile) and the Mochi
      profile (mochi_profile).
   j) Save config.json and close the F12 panel.

   Tips:
   - If no "update-info" line shows up, keep the panel open, refresh the page (F5),
     and switch profiles again.
   - The same Payload may also show user_name, user_gender, and user_backstory.
     You can copy those into config.json too, so they match what Kindroid has.
   - This is not an official Kindroid feature, just a way to see what the website
     sends. If Kindroid changes its website, the steps may look a little different.
   - If an ID is missing, the app will say so when it tries to switch profiles.

5. Double-click "Lora.bat" to start the app.

The very first time you open it, it may say "Add these to .env first" right away.
That just means it tried to start her morning before steps 3 and 4 were done.


HOW TO USE IT
-------------
1. Type her current setting in the box at the top, for example:
       lora on the porch swing with Mochi, sunny afternoon
2. Press "Change action".
3. That's it. The app keeps going on its own until you press "Stop".

What happens, over and over:
  a) ONE MINUTE BEFORE a change, the Narrator posts a heads-up in the chat. It tells
     Lora the change is coming and lists the commands she can use.
  b) Lora answers. If she uses a command, the app follows it.
  c) The change happens. The Narrator posts:
         *before, (where she was)
         then, (what happened in between, and why)
         now, (where she is now)*
         time until next change: 15 minutes
     and her "current setting" in Kindroid is updated to match.
  d) The timer starts again.

Every 10 minutes during a wait, the app also quietly reads the chat, so a command
she gives in the middle of a wait happens right then.


LORA'S COMMANDS (the Narrator tells her these every time)
---------------------------------------------------------
  "Narrator extend time please"          -> she stays where she is longer
  "hey @narrator" + what she wants       -> the Narrator does exactly that
  "narrator change weather to rain"      -> the weather changes, she stays put
  "narrator make it night"               -> something around her changes,
  "narrator turn the lights down"           she stays put
  "narrator add a hammock"

If she doesn't ask for anything, DeepSeek picks the next thing for her, based on the
time of day, where she is, and what she has been doing.

When she asks for something, the Narrator uses HER reason and shows only what she asked.
It does not add extra actions.


THE WINDOW
----------
Top:      her current setting, the "Change action" and "Stop" buttons, and a status line.

Buttons that open panels:
  Schedule     Every: how often she changes (like 10, or 2h).
               At: fixed times for a change (like 8:00 AM, noon, night).
               Wake at: when her day starts each morning.
               "DeepSeek decides how long each action lasts": when ticked, each action
                 lasts a realistic time (a snack 5-10 minutes, cooking 40-60, and so on)
                 instead of the Every number.
               "Narrator gives a 1-minute heads-up before each change": untick to turn
                 the heads-up off. Then "Change action" changes her right away.
  Mochi        "Mochi taps the wand": Mochi sends a line in the chat now and then.
               Talk every: how many minutes between his lines. Use 10 or more.
  World        What the app knows: her place, activity, weather, goal, and memories.
  Environment  The list of places and things in her world. Edit it to change her world.
  Backstory    Who she is, for DeepSeek.
  Pictures     Add pictures for a slideshow behind the window, and how see-through it is.


FILES
-----
  Lora.bat            Double-click to start.
  gui.py              The window.
  simulation.py       Her world: places, memories, goals, and planning the next step.
  update_scene.py     Talking to Kindroid and DeepSeek, and the Narrator's messages.
  wand.py             Mochi's wand.
  test_simulation.py  Automatic tests (run: python -m unittest test_simulation).
                      Most tests only run after her Environment list has been saved.
  config.json         Names and profile IDs (step 4).
  world_map.json      How places connect (for example, bedroom to outside by the glass doors).
  .env.example        A blank template for your keys (step 3).
  requirements.txt    The extra piece to install (step 2).

Files the app makes by itself (private, don't share them):
  .env                Your keys.
  state.json          Her world, memories, and your settings. state.backup.json is a spare copy.
  simulation.log      A diary of everything the app did. Handy when something seems off.


GOOD TO KNOW
------------
- Kindroid limits how often an app can read the chat (about 600 times a day) and how fast
  it can switch profiles. If you see "429 Too Many Requests", slow things down: a longer
  Every, and Mochi's "Talk every" at 10 minutes or more.
- The app only changes things while it is open.
- To see why something happened, open simulation.log and look at the lines around that time.
