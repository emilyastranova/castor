/** Emoji for reactions: a curated set with words to search by (no dependency), grouped as pickers
 *  usually are. Custom emoji (admins upload them) are ":name:" and come from lib/emojis.svelte.ts. */

export interface EmojiGroup {
	name: string;
	icon: string;
	/** [emoji, words to find it by] */
	items: [string, string][];
}

const g = (name: string, icon: string, text: string): EmojiGroup => ({
	name,
	icon,
	items: text
		.trim()
		.split('\n')
		.map((line) => {
			const [e, ...words] = line.trim().split(' ');
			return [e, words.join(' ')] as [string, string];
		})
});

/** The ones offered first, one click away. */
export const QUICK = ['👍', '❤️', '🎉', '😂', '👀', '🔥', '✅', '🙏'];

export const GROUPS: EmojiGroup[] = [
	g('Smileys', 'mood', `
😀 grin happy smile
😃 smile happy joy
😄 smile laugh happy
😁 grin beam
😆 laugh lol
😅 sweat relief nervous laugh
😂 joy laugh tears lol
🤣 rofl rolling laugh
🙂 slight smile
🙃 upside down silly
😉 wink
😊 blush smile
😇 angel innocent halo
🥰 love hearts adore
😍 heart eyes love
🤩 star struck wow
😘 kiss
😋 yum tasty
😛 tongue
😜 wink tongue crazy
🤪 zany crazy
🤔 thinking hmm
🤨 raised eyebrow skeptical
😐 neutral meh
😑 expressionless
😶 no mouth speechless
🙄 eye roll
😏 smirk
😬 grimace awkward yikes
🤥 lying
😌 relieved
😔 pensive sad
😪 sleepy
😴 sleeping zzz tired
😷 mask sick
🤒 sick fever
🤯 mind blown exploding head
🥳 party celebrate
😎 cool sunglasses
🤓 nerd geek
🧐 monocle inspect
😕 confused
😟 worried
🙁 frown sad
😮 open mouth surprised
😯 hushed
😲 astonished shocked
😳 flushed embarrassed
🥺 pleading puppy eyes
😢 cry sad tear
😭 sob crying
😱 scream fear
😖 confounded
😣 persevere
😞 disappointed
😓 sweat downcast
😩 weary
😫 tired
🥱 yawn bored
😤 triumph huff
😡 angry rage
😠 angry mad
🤬 swearing cursing
😈 devil smiling imp
💀 skull dead
☠️ skull crossbones danger
💩 poop
🤡 clown
👻 ghost
👽 alien
🤖 robot bot
😺 cat smile
🙈 see no evil monkey
🙉 hear no evil
🙊 speak no evil
`),
	g('People', 'waving_hand', `
👍 thumbs up yes +1 like agree
👎 thumbs down no -1 dislike
👌 ok perfect
🤌 pinched fingers chef
✌️ victory peace
🤞 fingers crossed luck
🤟 love you
🤘 rock horns metal
🤙 call me shaka
👈 point left
👉 point right
👆 point up
👇 point down
☝️ index up one
👋 wave hello bye
🤚 raised back hand
✋ raised hand stop high five
🖖 vulcan spock
👏 clap applause bravo
🙌 raising hands celebrate hooray
👐 open hands
🤲 palms up
🤝 handshake deal agree
🙏 pray thanks please
✍️ writing
💪 muscle strong flex
🦾 mechanical arm
🧠 brain smart
👀 eyes looking watching
👁️ eye
🫡 salute
🫠 melting
🫶 heart hands
🤦 facepalm
🤷 shrug dunno
🙋 raising hand me
🙆 ok gesture
🙅 no gesture
💁 tipping hand
🧑‍💻 technologist coder hacker
🕵️ detective investigate spy
🥷 ninja stealth
🧙 wizard mage
🦸 superhero
`),
	g('Hearts', 'favorite', `
❤️ red heart love
🧡 orange heart
💛 yellow heart
💚 green heart
💙 blue heart
💜 purple heart
🖤 black heart
🤍 white heart
🤎 brown heart
💔 broken heart
❣️ heart exclamation
💕 two hearts
💞 revolving hearts
💖 sparkling heart
💗 growing heart
💘 cupid arrow
💯 hundred perfect 100
💢 anger
💥 boom collision
💫 dizzy
💦 sweat drops
💨 dash fast
🕳️ hole
💬 speech bubble comment
🗯️ anger bubble
💭 thought bubble
💤 zzz sleep
`),
	g('Celebrate', 'celebration', `
🎉 tada party celebrate hooray
🎊 confetti
🥂 cheers toast
🍾 champagne bottle
🎁 gift present
🏆 trophy win
🥇 gold first medal
🥈 silver second
🥉 bronze third
🏅 medal
🎖️ military medal
⭐ star
🌟 glowing star
✨ sparkles new shiny
🎯 bullseye target direct hit
🎲 dice
🎮 game controller
🃏 joker
🚀 rocket launch ship it
🔥 fire hot lit
⚡ lightning zap fast
🌈 rainbow
☀️ sun
🌙 moon night
☕ coffee
🍕 pizza
🍺 beer
🍰 cake
🍪 cookie
🍿 popcorn
`),
	g('Animals', 'pets', `
🐶 dog
🐱 cat
🦊 fox
🐻 bear
🐼 panda
🐨 koala
🐯 tiger
🦁 lion
🐮 cow
🐷 pig
🐸 frog
🐵 monkey
🐔 chicken
🐧 penguin
🦉 owl
🦅 eagle
🦆 duck
🦄 unicorn
🐝 bee
🐛 bug
🦋 butterfly
🐌 snail slow
🐢 turtle slow
🐍 snake python
🦖 t-rex dinosaur
🐙 octopus
🦀 crab rust
🐟 fish
🐬 dolphin
🐳 whale docker
🦫 beaver
🌵 cactus
🌲 tree
🍀 clover luck
🌸 blossom flower
🌻 sunflower
`),
	g('Objects', 'build', `
✅ check done yes complete
☑️ checkbox
✔️ check mark
❌ cross no wrong
❎ cross mark
⚠️ warning caution
🚨 alert siren
🚫 prohibited no
⛔ no entry stop
❗ exclamation important
❓ question
❔ white question
‼️ double exclamation
⁉️ interrobang
💡 idea bulb
🔍 search magnify
🔎 search magnify right
🔒 locked secure
🔓 unlocked
🔑 key
🗝️ old key
🛡️ shield security
⚔️ swords attack
🗡️ dagger
🔫 water pistol
💣 bomb
🧨 firecracker dynamite
🔧 wrench fix
🔨 hammer
🛠️ tools
⚙️ gear settings
🧰 toolbox
🧲 magnet
🧪 test tube experiment
🔬 microscope
📡 satellite antenna
💻 laptop computer
🖥️ desktop
⌨️ keyboard
🖱️ mouse
🖨️ printer
📱 phone mobile
☎️ telephone
📞 phone call
📟 pager
🔌 plug
🔋 battery
💾 floppy save
💿 disc
📀 dvd
🗄️ cabinet
📁 folder
📂 open folder
📄 page document
📃 page curl
📑 bookmark tabs
📊 bar chart
📈 chart up increase
📉 chart down decrease
📋 clipboard
📌 pin pushpin
📍 round pin location
📎 paperclip attach
✂️ scissors cut
📝 memo note write
✏️ pencil edit
🖊️ pen
📅 calendar date
⏰ alarm clock
⏳ hourglass waiting
⌛ hourglass done
⏱️ stopwatch timer
📦 package box
📬 mailbox
📧 email
✉️ envelope mail
🏷️ label tag
🔖 bookmark
💰 money bag
💸 money flying
🧾 receipt
🏁 checkered flag finish
🚩 red flag
🏴‍☠️ pirate flag
🧭 compass
🗺️ map
🌐 globe web internet
🌍 earth world
🏠 house home
🏢 office building
🏭 factory
🏰 castle
🛰️ satellite
🚧 construction work in progress wip
🚦 traffic light
🛑 stop sign
`),
	g('Symbols', 'tag', `
➕ plus add
➖ minus
✖️ multiply
➗ divide
🟢 green circle
🟡 yellow circle
🟠 orange circle
🔴 red circle
🔵 blue circle
🟣 purple circle
⚫ black circle
⚪ white circle
🟩 green square
🟥 red square
🔺 red triangle up
🔻 red triangle down
⬆️ up arrow
⬇️ down arrow
⬅️ left arrow
➡️ right arrow
↩️ return back
🔁 repeat
🔄 refresh arrows
🆕 new
🆗 ok button
🆒 cool button
🆘 sos help
🔞 eighteen
♻️ recycle
🔰 beginner
🏳️ white flag
🏳️‍🌈 rainbow flag pride
🏳️‍⚧️ transgender flag
`)
];

/** Every emoji with its words, for search. */
export const ALL: [string, string][] = GROUPS.flatMap((gr) => gr.items);

export function searchEmoji(query: string, limit = 64): string[] {
	const q = query.trim().toLowerCase();
	if (!q) return [];
	const starts: string[] = [];
	const within: string[] = [];
	for (const [e, words] of ALL) {
		const ws = words.split(' ');
		if (ws.some((w) => w.startsWith(q))) starts.push(e);
		else if (words.includes(q)) within.push(e);
	}
	return [...starts, ...within].slice(0, limit);
}

/** The words for an emoji (a tooltip, an aria label). */
export function emojiName(e: string): string {
	const custom = /^:([a-z0-9_]+):$/.exec(e);
	if (custom) return custom[1].replaceAll('_', ' ');
	return ALL.find(([x]) => x === e)?.[1].split(' ')[0] ?? e;
}

export const isCustom = (e: string) => /^:[a-z0-9_]{2,32}:$/.test(e);
