<!-- <svelte:head>
    <script src="https://cdn.ckeditor.com/ckeditor5/41.2.0/classic/ckeditor.js"></script>
</svelte:head> -->

<script>
    import { onMount } from "svelte";
    onMount(async () => {
        await import("https://cdn.ckeditor.com/ckeditor5/41.2.0/classic/ckeditor.js");
        ClassicEditor
            .create(document.querySelector("#editor"))
            .catch((error) => {
                console.error(error);
            });
        });
</script>

<html>
    <div id="editor"></div>
</html>

