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
	{
	  key: "agent_id",
	  value: "Agent",},
	{
	  key: "start_date",
	  value: "Created At",
	}
  ];

  let rows = data.data;
//   Take the rows and add an id key to the object
  let keyed_rows = rows.map((row, index) => {
	  //   Convert any null values into a string
		Object.keys(row).forEach((key) => {
			if (row[key] === null) {
				row[key] = "N/A";
			}
		});
	return {
	  ...row,
	  id: index,
	};
  });

  const colorMap = {
	"pending": "gray",
	"completed": "green",
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
