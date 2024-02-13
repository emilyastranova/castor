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
	DataTable,
	} from "carbon-components-svelte";
    import CopyFile from "carbon-icons-svelte/lib/CopyFile.svelte";
  let selectedIds = [];
  let target;
  export let data;

  let columns = [
	{
	  key: "_id",
	  value: "ID",
	},
	{
		key: "name",
	value: "Name"
	},
	{
	  key: "status",
	  value: "Status",
	},
	{
	  key: "description",
	  value: "Description",
	},
  ];

  let rows = data.data;
//   Take the rows and add an id key to the object
  let keyed_rows = rows.map((row, index) => {
	return {
	  ...row,
	  id: index,
	};
  });
console.log(rows)
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
				<h1>Jobs</h1>
			</Column>
		</Row>
		<br />
		<Row>
			<Column>
				{#if rows}
					<DataTable rows={keyed_rows} headers={columns}/>
				{:else}
					<p>Loading...</p>
				{/if}
			</Column>
		</Row>
	</Grid>
</Content>
