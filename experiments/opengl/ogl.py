import ctypes
import glfw
import OpenGL
import OpenGL.GL

sizeof_float = ctypes.sizeof(OpenGL.GL.GLfloat)


def main():

    # Initialize the library
    if not glfw.init():
        return

    glfw.window_hint(glfw.SAMPLES, 4)
    glfw.window_hint(glfw.CONTEXT_VERSION_MAJOR, 3)
    glfw.window_hint(glfw.CONTEXT_VERSION_MINOR, 3)
    glfw.window_hint(glfw.OPENGL_FORWARD_COMPAT, True)
    glfw.window_hint(glfw.OPENGL_PROFILE, glfw.OPENGL_CORE_PROFILE)

    # Create a windowed mode window and its OpenGL context
    window = glfw.create_window(640, 480, "Hello World", None, None)
    if not window:
        glfw.terminate()
        return

    vertex_array_id = 0
    OpenGL.GL.glGenVertexArrays(1, vertex_array_id)
    OpenGL.GL.glBindVertexArray(vertex_array_id)

    vertex_buffer_data = [-1.0, -1.0, 0.0,
                          1.0, -1.0, 0.0,
                          0.0, 1.0, 0.0]
    array_type = (OpenGL.GL.GLfloat * len(vertex_buffer_data))
    vertex_buffer_id = OpenGL.GL.glGenBuffers(1)
    OpenGL.GL.glBindBuffer(OpenGL.GL.GL_ARRAY_BUFFER, vertex_buffer_id)
    OpenGL.GL.glBufferData(OpenGL.GL.GL_ARRAY_BUFFER,
                           len(vertex_buffer_data) * sizeof_float,
                           array_type(*vertex_buffer_data),
                           OpenGL.GL.GL_STATIC_DRAW)

    # Make the window's context current
    glfw.make_context_current(window)

    # Loop until the user closes the window
    while not glfw.window_should_close(window) and glfw.get_key(window, glfw.KEY_ESCAPE) != glfw.PRESS:
        # Render here, e.g. using pyOpenGL

        # Swap front and back buffers
        glfw.swap_buffers(window)

        # Poll for and process events
        glfw.poll_events()

    glfw.terminate()


if __name__ == "__main__":
    main()
