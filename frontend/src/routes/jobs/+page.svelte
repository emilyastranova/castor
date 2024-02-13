<script>
	import {
		Content,
		Grid,
		Row,
		Column,
        ContextMenu,
    ContextMenuDivider,
    ContextMenuGroup,
    ContextMenuOption,
	DataTable,
	Tag
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

  const colorMap = {
	"pending": "gray",
	"complete": "green",
	"failed": "red",
	"running": "purple",
  };

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
					<DataTable rows={keyed_rows} headers={columns}>
						<svelte:fragment slot="cell" let:row let:cell>
							{#if cell.key === "status"}
							  <Tag type={colorMap[cell.value]}>{cell.value}</Tag>
							{:else}
							  {cell.value}
							{/if}
						  </svelte:fragment>
					</DataTable>
				{:else}
					<p>Loading...</p>
				{/if}
			</Column>
		</Row>
	</Grid>
</Content>
