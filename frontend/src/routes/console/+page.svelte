<script>
	import { Content, Grid, Row, Column } from "carbon-components-svelte";
</script>

<svelte:head>
	<link rel="stylesheet" href="node_modules/xterm/css/xterm.css" />
	<script src="node_modules/xterm/lib/xterm.js"></script>
</svelte:head>

<Content>
	<Grid padding>
		<Row>
			<Column>
				<h1>Console</h1>
			</Column>
		</Row>
		<Row>
			<Column>
				<div id="terminal"></div>
				<script>
					// Make terminal tall
					var term = new Terminal({
						cols: 80,
						rows: 40,
						cursorBlink: true,
						theme: {
							background: "#000000",
							foreground: "#ffffff",
						},
					});
					term.open(document.getElementById("terminal"));
					var logo_ascii = `                                  \r
                @@@@@@@@@@@@			\r
             @@@@@@@@@@@@@@@@@@			\r
            @@@@@@@@    @@@@@@@@		\r
        @@@          @@          @@@    \r
       @@@@@@   @@@@@@@@@@@@   @@@@@@   \r
      @@@@@@   @@@@@@@@@@@@@@   @@@@@@  \r
      @@@@@@  @@@@@@@@@@@@@@@@  @@@@@@  \r
      @@@@@@   @@@@@@@@@@@@@@   @@@@@@  \r
       @@@@@@   @@@@@@@@@@@@   @@@@@@   \r
        @@@@@@@      @@      @@@@@@@   	\r
         @@@@@@@@@@@    @@@@@@@@@@@    	\r
            @@@@@@@@@  @@@@@@@@@       	\r
                @@@@@  @@@@@           	\r

\x1B[1;36m _____            _____ _______ ____  _____  \x1B[0m\r
\x1B[1;36m/ ____|    /\\    / ____|__   __/ __ \\|  __ \\ \x1B[0m\r
\x1B[1;36m| |       /  \\  | (___    | | | |  | | |__) |\x1B[0m\r
\x1B[1;36m| |      / /\\ \\  \\___ \\   | | | |  | |  _  / \x1B[0m\r
\x1B[1;36m| |____ / ____ \ ____) |   | | | |__| | | \\ \\ \x1B[0m\r
\x1B[1;36m\\_____ /_/    \\_\\_____/   |_|  \\____/|_|  \\_\\\x1B[0m\r
`;
					term.write("\x1B[1;29m" + logo_ascii + "\x1B[0m\n\n\r");
					term.write(
						"\x1B[1;32m\t\t Castor CLI\x1B[1;33m\n\r\t       Version: 0.0.1\x1B[0m\n\n\r"
					);
					term.write("$ ");
				</script>
			</Column>
		</Row>
	</Grid>
</Content>
