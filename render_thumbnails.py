import bpy
from typing import List
from math import radians
import os


bl_info = {
    "name": "Render asset thumbnail",
    "author": "GruntWorks",
    "blender": (4, 4, 0),
    "version": (0, 3, 0),
    "location": "ASSETS",
    "description": "Renders selected asset thumbnails from the Asset Browser to folders based on the current collection name.",
    "category": "User Interface",
}


DEFAULT_CAMERA_ROTATION = (
    radians(65.0),
    0.0,
    radians(40.0),
)


class RenderAssetsThumbnail(bpy.types.Operator):
    bl_idname = "asset.render_thumbnails"
    bl_label = "Render thumbnails"
    bl_description = "With one or more assets in the Asset Browser selected, render new thumbnails for them."

    allowed_types = {"COLLECTION", "OBJECT"}

    thumb_dir = ""
    visible_objects: list[bpy.types.Object] = []
    # This is to revert render settings after executing
    _settings: dict[str, dict] = {}

    ##################################################################
    # Asset / scene lookup
    ##################################################################

    @classmethod
    def poll(cls, context):
        return bool(context.selected_assets)

    def collection_in_scene(self, scene: bpy.types.Scene, collection: bpy.types.Collection) -> bool:
        return (
            collection == scene.collection
            or collection in scene.collection.children_recursive
        )

    def get_scene_for_asset(self, asset: bpy.types.FileSelectEntry) -> bpy.types.Scene | None:
        """
        Find a scene containing the selected asset.

        The current scene is preferred when an object belongs to
        multiple scenes.
        """
        local_id = asset.local_id
        current_scene = bpy.context.scene

        if isinstance(local_id, bpy.types.Object):
            scenes = local_id.users_scene
            if current_scene in scenes:
                return current_scene

            return scenes[0] if scenes else None

        if isinstance(local_id, bpy.types.Collection):
            if self.collection_in_scene(current_scene, local_id):
                return current_scene

            for scene in bpy.data.scenes:
                if self.collection_in_scene(scene, local_id):
                    return scene

        return None

    def get_collection_name(self, asset, scene: bpy.types.Scene | None = None) -> str:
        """
        Get the asset name (used for the directory where thumbnails are stored)

        For objects belonging to multiple collections, prefer one belonging to
            the asset's current scene.
        """
        if isinstance(asset, bpy.types.Object):
            if scene:
                for collection in asset.users_collection:
                    if self.collection_in_scene(scene, collection):
                        return collection.name

            if asset.users_collection:
                return asset.users_collection[0].name

            return asset.name

        if isinstance(asset, bpy.types.Collection):
            return asset.name

        return asset.name

    ##################################################################
    # Object / collection visibility
    ##################################################################

    def disable_visible_objects(self) -> None:
        self.visible_objects = [obj for obj in bpy.data.objects if not obj.hide_render]

        for obj in self.visible_objects:
            obj.hide_render = True


    def enable_visible_objects(self) -> None:
        for obj in self.visible_objects:
            obj.hide_render = False


    def enable_and_select(self, asset):
        if isinstance(asset, bpy.types.Object):
            asset.hide_render = False
            asset.select_set(True)
            bpy.context.view_layer.objects.active = asset
            return asset

        # Go through all objects in the collection
        if isinstance(asset, bpy.types.Collection):
            collection = bpy.data.collections.get(asset.name)
            if collection:
                self.select_all_objects_in_collection(collection)
                return collection

        return None


    def select_all_objects_in_collection(self, collection: bpy.types.Collection) -> None:
        """Recursively select all objects in the given collection and its child collections."""
        if not collection:
            return

        collection.hide_render = False

        for obj in collection.objects:
            obj.select_set(True)
            obj.hide_render = False

        for sub_collection in collection.children:
            self.select_all_objects_in_collection(sub_collection)

    ##################################################################
    # View / camera helpers
    ##################################################################

    def update_thumbnail(self, context, asset: bpy.types.FileSelectEntry, location: str) -> None:
        """Loads the PNG, sets it as the asset's preview file"""
        filepath = os.path.join(location, f"{asset.local_id.name}.png")

        if bpy.app.version >= (4, 0, 0):
            with context.temp_override(id=asset.local_id):
                bpy.ops.ed.lib_id_load_custom_preview(filepath=filepath)
        else:
            bpy.ops.ed.lib_id_load_custom_preview({"id": asset.local_id},filepath=filepath)

    def get_area_type(self, area_type: str) -> bpy.types.Area | None:
        if not area_type:
            return None

        for area in bpy.context.screen.areas:
            if area.type == area_type:
                return area

        return None

    def setup_camera_for_current_scene(self) -> None:
        """
        Prepare the current scene for rendering the thumbnail.

        If the scene already has a camera, its existing transform is preserved
            and used as the thumbnail camera angle.
        If the scene has no camera, create a temporary camera with the configured
            default thumbnail rotation.
        """
        scene = bpy.context.scene

        # Only initialize each scene once
        if scene.name in self._settings:
            return

        original_camera = scene.camera
        created_camera = None

        if original_camera is None:
            camera_data = bpy.data.cameras.new(name="__asset_thumbnail_camera")
            created_camera = bpy.data.objects.new(name="__asset_thumbnail_camera", object_data=camera_data)
            scene.collection.objects.link(created_camera)
            created_camera.rotation_euler = DEFAULT_CAMERA_ROTATION
            scene.camera = created_camera

        # Save the original scene data
        self._settings[scene.name] = {
            "resolution_x": scene.render.resolution_x,
            "resolution_y": scene.render.resolution_y,
            "film_transparent": scene.render.film_transparent,
            "use_nodes": scene.use_nodes,
            "file_format": scene.render.image_settings.file_format,
            "color_mode": scene.render.image_settings.color_mode,
            "filepath": scene.render.filepath,
            "frame_current": scene.frame_current,
            "camera": original_camera,
            "camera_matrix": (
                original_camera.matrix_world.copy()
                if original_camera is not None
                else None
            ),
            "clip_start": (
                original_camera.data.clip_start
                if original_camera is not None
                else None
            ),
            "created_camera": created_camera,
        }

        # Really close, for the small assets which usually end up closer to the camera
        scene.camera.data.clip_start = 1e-6

        # Render settings
        scene.render.resolution_x = 256
        scene.render.resolution_y = 256
        scene.render.film_transparent = True
        scene.use_nodes = False
        scene.render.image_settings.file_format = "PNG"
        scene.render.image_settings.color_mode = "RGBA"


    def restore_render_settings(self) -> None:
        """Restore every modified scene"""
        for scene_name, settings in self._settings.items():
            scene = bpy.data.scenes.get(scene_name)

            if scene is None:
                # Should never arrive here, unless a scene is deleted while rendering thumbnails
                continue

            scene.render.resolution_x = settings["resolution_x"]
            scene.render.resolution_y = settings["resolution_y"]
            scene.render.film_transparent = settings["film_transparent"]
            scene.use_nodes = settings["use_nodes"]
            scene.render.image_settings.file_format = settings["file_format"]
            scene.render.image_settings.color_mode = settings["color_mode"]
            scene.render.filepath = settings["filepath"]
            scene.frame_set(settings["frame_current"])

            original_camera = settings["camera"]

            # Restore original camera
            scene.camera = original_camera
            if (
                original_camera is not None
                and original_camera.name in bpy.data.objects
            ):
                if settings["camera_matrix"] is not None:
                    original_camera.matrix_world = settings["camera_matrix"]
                if settings["clip_start"] is not None:
                    original_camera.data.clip_start = settings["clip_start"]

            # Delete temporary camera if one was created
            created_camera = settings["created_camera"]
            if (
                created_camera is not None
                and created_camera.name in bpy.data.objects
            ):
                bpy.data.objects.remove(created_camera, do_unlink=True)

    ##################################################################
    # Rendering
    ##################################################################

    def render_thumbnail(self, context, assets: List[bpy.types.FileSelectEntry]) -> None:
        executed_objects = {}
        original_scene = context.window.scene
        window = context.window
        bpy.context.window_manager.progress_begin(0, len(assets))

        try:
            for idx, asset in enumerate(assets):
                if asset.id_type not in self.allowed_types:
                    executed_objects[asset.local_id.name] = "ERROR"
                    continue

                source_scene = self.get_scene_for_asset(asset)
                if source_scene is None:
                    executed_objects[asset.local_id.name] = "ERROR"
                    self.report(
                        {"WARNING"},
                        f"Could not find a scene containing '{asset.local_id.name}'",
                    )

                    continue

                # Switch the active window to that scene
                if window.scene != source_scene:
                    window.scene = source_scene

                # Configure this scene's camera
                source_scene.frame_set(idx)
                self.setup_camera_for_current_scene()

                # Clear current selection
                bpy.ops.object.select_all(action="DESELECT")

                # Select the asset (single object or entire collection of objects)
                active_obj = self.enable_and_select(asset.local_id)
                if not active_obj:
                    executed_objects[asset.local_id.name] = "ERROR"
                    self.report(
                        {"WARNING"},
                        (f"Could not select '{asset.local_id.name}'"),
                    )

                    continue

                # Output directory
                filename = bpy.path.basename(bpy.context.blend_data.filepath).replace(".blend", "")
                collection_name = self.get_collection_name(active_obj, source_scene)
                collection_dir = os.path.join(
                    self.thumb_dir,
                    f"{filename}_{''.join(collection_name.split())}"
                )
                os.makedirs(collection_dir, exist_ok=True)

                # Position camera
                bpy.ops.view3d.camera_to_view_selected()

                # Render
                output_filepath = os.path.join(collection_dir, f"{active_obj.name}.png")
                source_scene.render.filepath = output_filepath
                bpy.ops.render.render(write_still=True)

                # Set rendered image as the thumbnail
                self.update_thumbnail(context, asset, collection_dir)
                executed_objects[active_obj.name] = "INFO"

                # Hide this asset again before processing the next one
                active_obj.hide_render = True

                # Update progress (some mouse themes support a progress display, especially on linux)
                bpy.context.window_manager.progress_update(idx + 1)

        finally:
            # Return to the scene the user started in
            window.scene = original_scene

            bpy.context.window_manager.progress_end()

        # Show report
        for obj_name, status in executed_objects.items():
            self.report(
                {status},
                f"{'Updated' if status == 'INFO' else 'Skipped'} thumbnail for '{obj_name}'",
            )

        self.report({"OPERATOR"}, "Asset Catalog updated")

        bpy.ops.screen.info_log_show()

    ##################################################################
    # Setup
    ##################################################################

    def setup_directory(self) -> None:
        self.thumb_dir = os.path.join(os.path.dirname(bpy.data.filepath), "thumbnails")

    def check_initial_conditions(self):
        if not bpy.data.is_saved:
            self.report({"ERROR"}, "Please save current .blend file")
            return "err"

        area = self.get_area_type("VIEW_3D")
        if area is None:
            self.report({"ERROR"}, "A 3D Viewport is required")
            return "err"

        if area.spaces.active.region_3d.view_perspective == "CAMERA":
            area.spaces.active.region_3d.view_perspective = "PERSP"

        return None

    ##################################################################
    # Operator
    ##################################################################

    def execute(self, context):
        status = self.check_initial_conditions()

        if status == "err":
            return {"CANCELLED"}

        if (
            bpy.context.active_object
            and bpy.context.active_object.mode == "EDIT"
        ):
            bpy.ops.object.editmode_toggle()

        # Reset each time execute is called
        self.thumb_dir = ""
        self.visible_objects = []
        self._settings = {}

        self.setup_directory()
        os.makedirs(self.thumb_dir, exist_ok=True)
        self.disable_visible_objects()

        try:
            self.render_thumbnail(context, list(context.selected_assets))

        finally:
            # Restore everything, even if the above fails
            self.enable_visible_objects()
            self.restore_render_settings()

        return {"FINISHED"}



def display_button(self, context):
    self.layout.operator(RenderAssetsThumbnail.bl_idname)



##################################################################
# Registration
##################################################################

_MENU_FUNC_KEY = "render_asset_thumbnails_menu_func"
_OPERATOR_KEY = "render_asset_thumbnails_operator"


def get_asset_browser_menu():
    if hasattr(bpy.types, "ASSETBROWSER_MT_asset"):
        return bpy.types.ASSETBROWSER_MT_asset

    if hasattr(bpy.types, "ASSETBROWSER_MT_edit"):
        return bpy.types.ASSETBROWSER_MT_edit

    return None


def remove_registered_menu_func():
    menu = get_asset_browser_menu()
    if menu is None:
        return

    # Stored so that we have the same function object that was passed to append() in register()
    old_menu_func = bpy.app.driver_namespace.get(_MENU_FUNC_KEY)
    if old_menu_func is None:
        return

    try:
        menu.remove(old_menu_func)
    except Exception:
        pass

    bpy.app.driver_namespace.pop(_MENU_FUNC_KEY, None)


def unregister():
    # Remove menu entry
    remove_registered_menu_func()

    # Prefer the class actually registered with Blender rather than the newly-created
    #     Python class from a later Text Editor execution
    registered_class = bpy.app.driver_namespace.get(_OPERATOR_KEY)
    if registered_class is None:
        registered_class = getattr(bpy.types, RenderAssetsThumbnail.__name__, None)

    if registered_class is not None:
        try:
            bpy.utils.unregister_class(registered_class)
        except Exception:
            pass

    bpy.app.driver_namespace.pop(_OPERATOR_KEY, None)


def register():
    # Remove previous menu function and operator
    unregister()

    # Register above operator
    bpy.utils.register_class(RenderAssetsThumbnail)

    # Store the class for later un-registration
    bpy.app.driver_namespace[_OPERATOR_KEY] = RenderAssetsThumbnail

    menu = get_asset_browser_menu()

    if menu is None:
        print("Warning: no supported Asset Browser menu was found.")
        return

    # Register current menu callback
    menu.append(display_button)

    # Store function object for future removal
    bpy.app.driver_namespace[_MENU_FUNC_KEY] = display_button


if __name__ == "__main__":
    register()
