<template>
    <section id="useridle" class="plugin">
        <span>UI Idle: {{ value }}</span>
    </section>
</template>

<script>
export default {
    props: {
        data: {
            type: Object
        }
    },
    computed: {
        value() {
            return this.data.stats['useridle'];
        }
    }
};
</script>