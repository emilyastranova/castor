<script>
	import {
		Button,
		Content,
		Grid,
		Row,
		Column,
		TextArea,
		TextInput,
		Tag,
		Tabs,
		Tab,
		TabContent
	} from "carbon-components-svelte";
	/** @type {import('./$types').PageData} */
	// import Editor from "$lib/Editor.svelte"
	export let data;
	let task_data = data.task_data;
	console.log(task_data);
	// Make list of keys that don't need to be in the properties panel
	let ignore_keys = ["_id", "id", "title", "description", "comments", "screenshots", "status", "tags", "tasks"];
	// Map of status colors
	const colorMap = {
	"todo": "purple",
	"completed": "green",
	"blocked": "red",
	"in_progress": "blue",
  };
  	// Table headers for properties panel
	const headers = [
		{
			key: "key",
			value: "Property",
		},
		{
			key: "value",
			value: "Value",
		},
	];
</script>

<Content>
	<Grid>
		<Row>
			<!-- Main panel -->
			<Column sm={{ span: 3 }}>
				<Row>
					<Column>
						<a href="/tasks">Back to Tasks</a>
						<br/>
						<br/>
						<h3>
							{task_data.title}
						</h3>
					</Column>
				</Row>
				<br />
				<Row>
					<Column>
						<!-- Tabbed panel -->
						<Tabs>
							<Tab label="Description" />
							<Tab label="Logs" />
							<svelte:fragment slot="content">
								<TabContent>
									<TextArea labelText="Task description" placeholder="Enter a description..." value={task_data.description} />
								</TabContent>
							<TabContent>
								<TextArea labelText="Task logs" placeholder="Enter logs..." value={task_data.logs} />
							</TabContent>
							</svelte:fragment>
						</Tabs>
						<!-- Comments -->
						<h4>Comments</h4>
						<hr />
						<br />
						<!-- New Comment -->
						<TextArea labelText="New Comment" placeholder="Enter a comment..." />
						<!-- Save and cancel buttons -->
						<br />
						<Button kind="primary">Save</Button>
						<Button kind="secondary">Cancel</Button>
						<!-- Display comments -->
						<!-- TODO -->
					</Column>
				</Row>
			</Column>
			<!-- Info Panel -->
			<Column sm={{ span: 1}}>
				<Row>
					<Column>
						<h4>Status: <Tag type={colorMap[task_data.status]}>{task_data.status}</Tag></h4>
						<br/>
						<h4>
							Properties
						</h4>
						<hr />
						<br />
						<!-- Display TextInput fields for each thing in the task_data object -->
						{#each Object.keys(task_data) as key}
							{#if ignore_keys.indexOf(key) === -1}
								<TextInput labelText={key} value={task_data[key]} readonly />
							{/if}
						{/each}
					</Column>
				</Row>
			</Column>
		</Row>
	</Grid>
</Content>
