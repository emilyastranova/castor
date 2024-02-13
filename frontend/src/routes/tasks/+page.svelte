<script>
	import {
		Content,
		Grid,
		Row,
		Column,
		ClickableTile
	} from "carbon-components-svelte";
  export let data;
  let tasks = data.data;
</script>

<Content>
	<Grid>
		<Row>
			<Column>
				<h1>Tasks</h1>
			</Column>
		</Row>
		<br />
		<Row>
			<Column>
				<h3>Todo</h3>
			</Column>

			<Column>
				<h3>In-Progress</h3>
			</Column>
			<Column>
				<h3>Complete</h3>
			</Column>
            <Column>
				<h3>Blocked</h3>
			</Column>
		</Row>
		<br />
		<Row>
			<!-- Todo Column -->
			<Column>
				<!-- If task.status is todo -->
				{#each tasks as task}
					{#if task.status === "todo"}
						<ClickableTile class="mb-4" href="/tasks/{task._id}">
							<h4>{task.title}</h4>
							<p>{task.description}</p>
						</ClickableTile>
					{/if}
				{/each}
			</Column>

			<!-- In-Progress Column -->
			<Column>
				<!-- If task.status is in_progress -->
				{#each tasks as task}
					{#if task.status === "in_progress"}
						<ClickableTile class="mb-4" href="/tasks/{task._id}" >
							<h4>{task.title}</h4>
							<p>{task.description}</p>
						</ClickableTile>
					{/if}
				{/each}
			</Column>

			<!-- Complete Column -->
			<Column>
				<!-- If task.status is complete -->
				{#each tasks as task}
					{#if task.status === "complete"}
						<ClickableTile class="mb-4" href="/tasks/{task._id}">
							<h4>{task.title}</h4>
							<p>{task.description}</p>
						</ClickableTile>
					{/if}
				{/each}
			</Column>
            <Column>
				<!-- If task.status is blocked -->
				{#each tasks as task}
					{#if task.status === "blocked"}
						<ClickableTile class="mb-4" href="/tasks/{task._id}">
							<h4>{task.title}</h4>
							<p>{task.description}</p>
						</ClickableTile>
					{/if}
				{/each}
			</Column>
		</Row>
	</Grid>
</Content>
