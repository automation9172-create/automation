# Shorts format report

This is a format study for the public `Singaroo KIDS` Shorts shelf, not a copy of its videos, audio, branding, or scripts.

## Repeated formats observed

- Fast educational series built around a single learning idea: vehicle names, dinosaur names, sea animals, weather words, colors, alphabet groups, and counting.
- Strong searchable title formula: `Learn/Sing/Guess + topic + age/learning context`, often with a small group of related objects in each episode.
- Short episodes use a hook first, then one item per beat, large readable words, bright object art, and a clear repeat/sing-along prompt.
- The catalog is made from repeatable families rather than one-off stories: A-D/E-H/I-L/M-P/Q-T/U-Z, vehicle groups, fruit counting, and “what is this?” quizzes.

The channel’s public metadata describes colorful educational songs and stories about letters, numbers, feelings, and healthy habits. Public shelf metadata does not establish where its background music was licensed. This project therefore does not copy or claim that channel’s audio. It uses the existing local `assets/back_musics` library and local teacher/student voices.

## Our independent Shorts design

- 1080x1920 vertical MP4, readable text, 3–4 scenes, roughly 20–35 seconds depending on the narration takes.
- Each object now follows a clear teacher voice → short pause → student voice rhythm; the voices are not mixed on top of each other.
- Backgrounds rotate per scene and the music is selected from the local licensed/owned music library, ducked under narration so it remains audible.
- A topic hook in the first scene, one PNG and spoken word per beat, and a final sing/say prompt.
- Five scheduled Shorts per day, with deterministic planning and `.shorts_work/plan_history.json` preventing exact signature repeats across runs.
- A failed individual render/upload does not stop the other Shorts; the job reports failure after attempting the whole batch.
- The long-video engine is not imported and is not modified.
