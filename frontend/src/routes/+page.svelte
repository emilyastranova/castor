<script>
	import { Content, Grid, Row, Column, ClickableTile, Tag, TextArea, Button } from "carbon-components-svelte";
	import SendAlt from "carbon-icons-svelte/lib/SendAlt.svelte";
	import Hashtag from "carbon-icons-svelte/lib/Hashtag.svelte";
	import Image from "carbon-icons-svelte/lib/Image.svelte";
	import CheckboxIndeterminate from "carbon-icons-svelte/lib/CheckboxIndeterminate.svelte";
	import Link from "carbon-icons-svelte/lib/Link.svelte";

</script>

<Content>
	<Grid>
		<Row>
			<Column sm={{ span: 3}}>
				<h2>Activity Feed</h2>
				<!-- Create post with text box -->
				<TextArea class="mb-2" id="post" labelText="Create a post" placeholder="Enter your post here" />
				<!-- Spread buttons to far left and right -->
				<div class="flex justify-between">
					<div class="flex">
						<Button icon={Hashtag} kind="ghost" iconDescription="Tags" />
						<Button icon={Image} kind="ghost" iconDescription="Add Image" />
						<Button icon={CheckboxIndeterminate} kind="ghost" iconDescription="Content Syntax" />
						<Button icon={Link} kind="ghost" iconDescription="Add Reference" />
					</div>
					<Button icon={SendAlt} kind="primary">Send</Button>
				</div>
				<!-- Display dummy "posts" -->
				<ClickableTile class="my-4">
					<div class="flex items-center mb-2">
						<img src="https://via.placeholder.com/50" alt="placeholder" class="rounded-full w-6 h-6" />
						<p class="text-sm ml-2">
							<span class="text-gray-500">@emilyastranova</span>
							<span class="mx-1 text-gray-500"> • </span>
							<span class="text-gray-500">20 minutes ago</span>
						</p>
					</div>
					<p>Lorem ipsum dolor sit amet, consectetur adipiscing elit. Nulla accumsan, metus ultrices eleifend gravida, nulla nunc varius lectus, nec rutrum justo nibh eu lectus. Ut vulputate semper dui. Fusce erat odio, sollicitudin vel erat vel, interdum mattis neque.</p>
				</ClickableTile>
				<ClickableTile class="my-4">
					<div class="flex items-center mb-2">
						<img src="https://via.placeholder.com/50" alt="placeholder" class="rounded-full w-6 h-6" />
						<p class="text-sm ml-2">
							<span class="text-gray-500">@castor_bot</span>
							<span class="mx-1 text-gray-500"> • </span>
							<span class="mr-1 text-gray-500">1 hour ago</span>
							<Tag type="purple">automated</Tag>
						</p>
					</div>
					<p>Task: "Scan the Borealis network" moved to <Tag type="green">completed</Tag></p>
				</ClickableTile>
				<ClickableTile class="my-4">
					<!-- Profile picture and username aligned -->
					<div class="flex items-center mb-2">
						<img src="https://via.placeholder.com/50" alt="placeholder" class="rounded-full w-6 h-6" />
						<p class="text-sm ml-2">
							<span class="text-gray-500">@emilyastranova</span>
							<span class="mx-1 text-gray-500"> • </span>
							<span class="text-gray-500">8 hours ago</span>
						</p>
					</div>
					<p>Lorem ipsum dolor sit amet, consectetur adipiscing elit. Nulla accumsan, metus ultrices eleifend gravida, nulla nunc varius lectus, nec rutrum justo nibh eu lectus. Ut vulputate semper dui. Fusce erat odio, sollicitudin vel erat vel, interdum mattis neque.</p>
					<!-- Insert 2 dummy pictures side-by-side, make them take up the entire width -->
					<div class="flex gap-2 mt-4">
						<img src="https://via.placeholder.com/250" alt="placeholder" class="w-1/2" />
						<img src="https://via.placeholder.com/250" alt="placeholder" class="w-1/2" />
					</div>
					
				</ClickableTile>
				{#each [1, 2, 3] as post}
					<ClickableTile class="my-4">
						<!-- Small text for time, slightly bigger for content -->
						<div class="flex items-center mb-2">
							<img src="https://via.placeholder.com/50" alt="placeholder" class="rounded-full w-6 h-6" />
							<p class="text-sm ml-2">
								<span class="text-gray-500">@emilyastranova</span>
								<span class="mx-1 text-gray-500"> • </span>
								<span class="text-gray-500">03/14/2024, 12:0{post} PM</span>
							</p>
						</div>
						<!-- Content -->
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
