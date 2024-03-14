<script>
	import { Content, Grid, Row, Column, ClickableTile, Tag, TextArea, Button } from "carbon-components-svelte";
	import SendAlt from "carbon-icons-svelte/lib/SendAlt.svelte";

</script>

<Content>
	<Grid>
		<Row>
			<Column>
				<h1>Dashboard</h1>
			</Column>
		</Row>
		<br/>
		<Row>
			<Column sm={{ span: 3}}>
				<h2>Feed</h2>
				<!-- Create post with text box -->
				<TextArea id="post" labelText="Create a post" placeholder="Enter your post here" />
				<Button icon={SendAlt} kind="primary">Send</Button>
				<!-- Display dummy "posts" -->
				{#each [1, 2, 3, 4, 5] as post}
					<ClickableTile class="my-4">
						<h4>Post {post}</h4>
						<p>Lorem ipsum dolor sit amet, consectetur adipiscing elit. Nulla accumsan, metus ultrices eleifend gravida, nulla nunc varius lectus, nec rutrum justo nibh eu lectus. Ut vulputate semper dui. Fusce erat odio, sollicitudin vel erat vel, interdum mattis neque.</p>
					</ClickableTile>
				{/each}
			</Column>
			<Column sm={{ span: 1}}>
				<h2>Tags</h2>
				<div class="my-4">
					<!-- Display dummy "tags" -->
					<Tag type="blue">#priv-esc</Tag>
					<Tag type="red">#exfil</Tag>
					<Tag type="green">#adcs</Tag>
					<Tag type="purple">#execute-assembly</Tag>
				</div>
			</Column>
		</Row>
	</Grid>
</Content>
