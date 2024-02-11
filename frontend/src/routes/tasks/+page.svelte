<script>
	import {
		Content,
		Grid,
		Row,
		Column,
		ClickableTile,
        ContextMenu,
    ContextMenuDivider,
    ContextMenuGroup,
    ContextMenuOption,
	} from "carbon-components-svelte";
    import CopyFile from "carbon-icons-svelte/lib/CopyFile.svelte";
  let selectedIds = [];

  $: console.log("selectedIds", selectedIds);
  let target;
  export let data;
  console.log(data);
</script>
<ContextMenu {target}>
    <ContextMenuOption
      indented
      labelText="Copy"
      shortcutText="⌘C"
      icon={CopyFile}
    />
    <ContextMenuDivider />
    <ContextMenuOption indented labelText="Change status">
      <ContextMenuGroup labelText="Move to" bind:selectedIds>
        <ContextMenuOption id="todo" labelText="Todo" />
        <ContextMenuOption id="inprogress" labelText="In-Progress" />
        <ContextMenuOption id="complete" labelText="Complete" />
        <ContextMenuOption id="blocked" labelText="Blocked" />
      </ContextMenuGroup>
    </ContextMenuOption>
    <ContextMenuDivider />
    <ContextMenuOption indented kind="danger" labelText="Delete" />
  </ContextMenu>
  
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
                <div bind:this={target}>

                    <ClickableTile href="/tasks/1">
                        <h4>Task 1</h4>
                        <p>Task 1 description</p>
                    </ClickableTile>
                </div>
			</Column>

			<!-- In-Progress Column -->
			<Column>
				<ClickableTile href="/tasks/2">
					<h4>Task 2</h4>
					<p>Task 2 description</p>
				</ClickableTile>
                <ClickableTile href="/tasks/1" class="mt-4">
					<h4>Task 4</h4>
					<p>Task 4 description</p>
				</ClickableTile>
			</Column>

			<!-- Complete Column -->
			<Column>
				<ClickableTile href="/tasks/3">
					<h4>Task 3</h4>
					<p>Task 3 description</p>
				</ClickableTile>
			</Column>
            <Column>
				<!-- None blocked, placeholder -->
                <ClickableTile disabled href="https://www.carbondesignsystem.com/">
                    <h4>None</h4>
                    <p>No tasks</p>
                  </ClickableTile>
                  
			</Column>
		</Row>
	</Grid>
</Content>
