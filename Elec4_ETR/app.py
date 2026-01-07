from flask import Flask, render_template, request, redirect, url_for, flash, session
from flask_mysqldb import MySQL
from forms import RegisterForm, LoginForm, ProductForm
import os
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from exports.sales_export import export_sales_pdf, export_sales_docx
from datetime import datetime, timedelta

app = Flask(__name__)

app.config['MYSQL_HOST'] = 'localhost'
app.config['MYSQL_USER'] = 'root'
app.config['MYSQL_PASSWORD'] = ''
app.config['MYSQL_DB'] = 'flask_ecommerce'
app.config['MYSQL_CURSORCLASS'] = 'DictCursor'
app.config['SECRET_KEY'] = 'secret123'

UPLOAD_FOLDER = 'static/uploads/payments'

UPLOAD_FOLDER = 'static/uploads/products'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

mysql = MySQL(app)

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS
           
@app.route('/register', methods=['GET', 'POST'])
def register():
    form = RegisterForm()

    if form.validate_on_submit():
        hashed_password = generate_password_hash(
            form.password.data,
            method='pbkdf2:sha256'
        )

        cur = mysql.connection.cursor()
        cur.execute(
            "INSERT INTO users (fullname, email, password) VALUES (%s, %s, %s)",
            (form.fullname.data, form.email.data, hashed_password)
        )
        mysql.connection.commit()
        cur.close()

        flash("Registered successfully!", "success")
        return redirect(url_for('login'))

    return render_template('auth/register.html', form=form)


@app.route('/login', methods=['GET', 'POST'])
def login():
    form = LoginForm()

    if form.validate_on_submit():
        cur = mysql.connection.cursor()
        cur.execute(
            "SELECT * FROM users WHERE email = %s",
            (form.email.data,)
        )
        user = cur.fetchone()
        cur.close()

        if user and check_password_hash(user['password'], form.password.data):
            session['user_id'] = user['id']
            session['role'] = user['role']
            flash("Login successful!", "success")
            return redirect(url_for('home'))
        else:
            flash("Invalid email or password", "danger")

    return render_template('auth/login.html', form=form)

@app.route('/')
def home():
    return render_template('home.html')

@app.route('/products')
def products():
    page = request.args.get('page', 1, type=int)
    limit = 20
    offset = (page - 1) * limit

    search = request.args.get('search', '')
    category = request.args.get('category', '')
    condition = request.args.get('condition', '')
    brand = request.args.get('brand', '')
    stock_status = request.args.get('stock_status', '')

    cur = mysql.connection.cursor()

    base_query = """
        FROM products
        JOIN categories ON products.category_id = categories.id
        WHERE 1=1
    """
    params = []

    if search:
        base_query += " AND products.name LIKE %s"
        params.append(f"%{search}%")

    if category:
        base_query += " AND categories.id = %s"
        params.append(category)

    if condition:
        base_query += " AND products.condition_type = %s"
        params.append(condition)

    if brand:
        base_query += " AND products.brand = %s"
        params.append(brand)

    if stock_status == 'available':
        base_query += " AND products.stock > 0"

    if stock_status == 'soldout':
        base_query += " AND products.stock = 0"

    # Count total products
    cur.execute("SELECT COUNT(*) AS total " + base_query, params)
    total = cur.fetchone()['total']
    total_pages = (total + limit - 1) // limit

    # Fetch paginated products
    query = """
    SELECT products.*, categories.name AS category
    """ + base_query + """
        ORDER BY products.stock = 0, products.id DESC
        LIMIT %s OFFSET %s
    """

    cur.execute(query, params + [limit, offset])
    products = cur.fetchall()

    # Get brands for filter
    cur.execute("SELECT DISTINCT brand FROM products WHERE brand IS NOT NULL")
    brands = cur.fetchall()

    # Get categories
    cur.execute("SELECT id, name FROM categories")
    categories = cur.fetchall()

    cur.close()

    return render_template(
        'products/products.html',
        products=products,
        categories=categories,
        brands=brands,
        page=page,
        total_pages=total_pages,
        search=search,
        category=category,
        condition=condition,
        brand=brand,
        stock_status=stock_status
    )

@app.route('/add-to-cart/<int:id>')
def add_to_cart(id):
    if 'user_id' not in session:
        return redirect(url_for('login'))

    user_id = session['user_id']
    cur = mysql.connection.cursor()

    # Check if product already in cart
    cur.execute("""
        SELECT quantity FROM cart_items
        WHERE user_id=%s AND product_id=%s
    """, (user_id, id))
    item = cur.fetchone()

    if item:
        cur.execute("""
            UPDATE cart_items
            SET quantity = quantity + 1
            WHERE user_id=%s AND product_id=%s
        """, (user_id, id))
    else:
        cur.execute("""
            INSERT INTO cart_items(user_id, product_id, quantity)
            VALUES(%s,%s,1)
        """, (user_id, id))

    mysql.connection.commit()
    cur.close()

    flash("Added to cart!", "success")
    return redirect(url_for('products'))

@app.route('/cart')
def cart():
    user_id = session['user_id']
    cur = mysql.connection.cursor()

    cur.execute("""
        SELECT p.id, p.name, p.price, p.stock, c.quantity,
            (p.price * c.quantity) AS subtotal
        FROM cart_items c
        JOIN products p ON c.product_id = p.id
        WHERE c.user_id = %s
    """, (user_id,))
    items = cur.fetchall()

    total = sum(item['subtotal'] for item in items)

    cur.close()
    return render_template('cart/cart.html', items=items, total=total)

@app.route('/update-cart', methods=['POST'])
def update_cart():
    user_id = session['user_id']
    product_id = request.form['product_id']
    quantity = int(request.form['quantity'])

    cur = mysql.connection.cursor()

    if quantity > 0:
        cur.execute("""
            UPDATE cart_items
            SET quantity=%s
            WHERE user_id=%s AND product_id=%s
        """, (quantity, user_id, product_id))
    else:
        cur.execute("""
            DELETE FROM cart_items
            WHERE user_id=%s AND product_id=%s
        """, (user_id, product_id))

    mysql.connection.commit()
    cur.close()
    return redirect(url_for('cart'))

@app.route('/remove-from-cart/<int:id>')
def remove_from_cart(id):
    user_id = session['user_id']
    cur = mysql.connection.cursor()
    cur.execute("""
        DELETE FROM cart_items
        WHERE user_id=%s AND product_id=%s
    """, (user_id, id))
    mysql.connection.commit()
    cur.close()
    return redirect(url_for('cart'))

@app.route('/checkout', methods=['POST'])
def checkout():
    user_id = session['user_id']
    payment_method = request.form['payment_method']
    proof_filename = None

    selected_ids = request.form.getlist('selected_items')  # list of selected product IDs
    if not selected_ids:
        flash("Please select at least one item to checkout.", "warning")
        return redirect(url_for('cart'))

    # Handle proof upload for online payment
    if payment_method == 'Online Payment':
        proof = request.files.get('payment_proof')
        if not proof or proof.filename == '':
            flash("Please upload proof of payment.", "danger")
            return redirect(url_for('cart'))
        proof_filename = secure_filename(proof.filename)
        proof.save(os.path.join(app.config['UPLOAD_FOLDER'], proof_filename))

    cur = mysql.connection.cursor()

    # Create order
    cur.execute("""
        INSERT INTO orders(user_id, payment_method, payment_proof)
        VALUES(%s,%s,%s)
    """, (user_id, payment_method, proof_filename))
    order_id = cur.lastrowid

    # Get selected cart items
    format_strings = ','.join(['%s'] * len(selected_ids))
    cur.execute(f"""
        SELECT c.product_id, c.quantity, p.price, p.stock, p.name
        FROM cart_items c
        JOIN products p ON c.product_id = p.id
        WHERE c.user_id=%s AND c.product_id IN ({format_strings})
    """, (user_id, *selected_ids))
    cart_items = cur.fetchall()

    # Insert order items and reduce stock
    for item in cart_items:
        # Get quantity from form
        qty_field = f'quantity_{item["product_id"]}'
        quantity = int(request.form.get(qty_field, item['quantity']))  # default to DB quantity

        # Check stock
        if quantity > item['stock']:
            flash(f"Not enough stock for {item['name']}. Max available: {item['stock']}", "danger")
            return redirect(url_for('cart'))

        # Reduce stock
        new_stock = item['stock'] - quantity
        cur.execute("UPDATE products SET stock=%s WHERE id=%s", (new_stock, item['product_id']))

        # Insert into order_items
        cur.execute("""
            INSERT INTO order_items(order_id, product_id, quantity, price)
            VALUES(%s,%s,%s,%s)
        """, (order_id, item['product_id'], quantity, item['price']))

        # Remove from cart
        cur.execute("""DELETE FROM cart_items WHERE user_id=%s AND product_id=%s""",
                    (user_id, item['product_id']))

    mysql.connection.commit()
    cur.close()
    flash("Order placed successfully!", "success")
    return redirect(url_for('orders'))

@app.route('/orders')
def orders():
    if 'user_id' not in session:
        flash("Please login first", "warning")
        return redirect(url_for('login'))

    user_id = session['user_id']
    status = request.args.get('status', 'Processing')
    page = request.args.get('page', 1, type=int)
    limit = 10
    offset = (page - 1) * limit

    cur = mysql.connection.cursor()

    # 🔢 Get order counts per status
    cur.execute("""
        SELECT status, COUNT(*) AS total
        FROM orders
        WHERE user_id = %s
        GROUP BY status
    """, (user_id,))
    rows = cur.fetchall()

    # initialize counts
    counts = {
        'Processing': 0,  # Pending + Approved
        'Shipped': 0,
        'Delivered': 0,
        'Declined': 0
    }

    # fill counts
    for r in rows:
        if r['status'] in ['Pending', 'Approved']:
            counts['Processing'] += r['total']
        elif r['status'] in counts:
            counts[r['status']] = r['total']

    # Fetch total for pagination
    if status == "Processing":
        cur.execute("""
            SELECT COUNT(*) AS total
            FROM orders
            WHERE user_id=%s AND status IN ('Pending', 'Approved')
        """, (user_id,))
    else:
        cur.execute("""
            SELECT COUNT(*) AS total
            FROM orders
            WHERE user_id=%s AND status=%s
        """, (user_id, status))
    total = cur.fetchone()['total']
    total_pages = (total + limit - 1) // limit

    # Fetch paginated orders
    if status == "Processing":
        cur.execute("""
            SELECT *
            FROM orders
            WHERE user_id=%s AND status IN ('Pending', 'Approved')
            ORDER BY created_at DESC
            LIMIT %s OFFSET %s
        """, (user_id, limit, offset))
    else:
        cur.execute("""
            SELECT *
            FROM orders
            WHERE user_id=%s AND status=%s
            ORDER BY created_at DESC
            LIMIT %s OFFSET %s
        """, (user_id, status, limit, offset))
    orders = cur.fetchall()

    cur.close()

    return render_template(
        'orders/orders.html',
        orders=orders,
        status=status,
        page=page,
        total_pages=total_pages,
        counts=counts
    )

@app.route('/orders/<int:id>')
def order_details(id):
    if 'user_id' not in session:
        return redirect(url_for('login'))

    cur = mysql.connection.cursor()

    # Order info
    cur.execute("""
        SELECT * FROM orders
        WHERE id=%s AND user_id=%s
    """, (id, session['user_id']))
    order = cur.fetchone()

    # Order items
    cur.execute("""
        SELECT oi.quantity, oi.price, p.name
        FROM order_items oi
        JOIN products p ON oi.product_id = p.id
        WHERE oi.order_id=%s
    """, (id,))
    items = cur.fetchall()

    # ✅ SUM TOTAL
    total = sum(item['quantity'] * item['price'] for item in items)

    cur.close()
    print(total)
    
    return render_template(
        'orders/order_details.html',
        order=order,
        items=items,
        total=total
    )

@app.route('/logout')
def logout(): 
    session.clear()
    return redirect(url_for('login'))

# ADMIN ROUTES
@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']

        cur = mysql.connection.cursor()
        cur.execute("""
            SELECT * FROM users
            WHERE email=%s AND role='admin'
        """, (email,))
        admin = cur.fetchone()
        cur.close()

        if admin and check_password_hash(admin['password'], password):
            session['admin_id'] = admin['id']
            session['admin_name'] = admin['fullname']
            session['role'] = admin['role']
            return redirect(url_for('admin_dashboard'))

        flash('Invalid admin credentials')

    return render_template('admin/login.html')

@app.route('/admin/register', methods=['GET', 'POST'])
def admin_register():
    if request.method == 'POST':
        fullname = request.form['fullname']
        email = request.form['email']
        password = generate_password_hash(request.form['password'])

        cur = mysql.connection.cursor()
        cur.execute("""
            INSERT INTO users (fullname, email, password, role)
            VALUES (%s, %s, %s, 'admin')
        """, (fullname, email, password))
        mysql.connection.commit()
        cur.close()

        flash('Admin registered successfully')
        return redirect(url_for('admin_login'))

    return render_template('admin/register.html')

@app.route('/admin/dashboard', methods=['GET', 'POST'])
def admin_dashboard():
    if session.get('role') != 'admin':
        flash("Unauthorized access", "danger")
        return redirect(url_for('home'))

    cur = mysql.connection.cursor()

    # ADD PRODUCT
    if request.method == 'POST':
        image_filename = None

        if 'image' in request.files:
            file = request.files['image']
            if file and allowed_file(file.filename):
                image_filename = secure_filename(file.filename)
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], image_filename))

        cur.execute("""
            INSERT INTO products
            (name, description, price, stock, condition_type, category_id, image)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
        """, (
            request.form['name'],
            request.form['description'],
            request.form['price'],
            request.form['stock'],
            request.form['condition'],
            request.form['category_id'],
            image_filename
        ))
        mysql.connection.commit()
        flash("Product added successfully", "success")

    # GET PRODUCTS
    cur.execute("""
        SELECT products.*, categories.name AS category
        FROM products
        JOIN categories ON products.category_id = categories.id
    """)
    products = cur.fetchall()

    # GET CATEGORIES
    cur.execute("SELECT * FROM categories")
    categories = cur.fetchall()

    cur.close()

    return render_template(
        'admin/dashboard.html',
        products=products,
        categories=categories
    )

# ADMIN PRODUCT MANAGEMENT
@app.route('/admin/products', methods=['GET', 'POST'])
def admin_products():
    if 'admin_id' not in session:
        flash("Unauthorized access", "danger")
        return redirect(url_for('home'))

    cur = mysql.connection.cursor()

    # ADD PRODUCT
    if request.method == 'POST':
        image_filename = None
        if 'image' in request.files:
            file = request.files['image']
            if file and file.filename != '' and allowed_file(file.filename):
                image_filename = secure_filename(file.filename)
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], image_filename))

        cur.execute("""
            INSERT INTO products
            (name, description, price, stock, condition_type, category_id, image)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
        """, (
            request.form['name'],
            request.form['description'],
            request.form['price'],
            request.form['stock'],
            request.form['condition'],
            request.form['category_id'],
            image_filename
        ))
        mysql.connection.commit()
        flash("Product added successfully", "success")

    # FILTER AND SEARCH
    category_filter = request.args.get('category')
    search_query = request.args.get('search', '').strip()
    condition_filter = request.args.get('condition', '')
    brand_filter = request.args.get('brand', '')
    stock_status = request.args.get('stock_status', '')

    # Build query with filters
    base_query = """
        SELECT p.*, c.name AS category
        FROM products p
        JOIN categories c ON p.category_id = c.id
        WHERE 1=1
    """
    params = []

    if category_filter:
        base_query += " AND c.id = %s"
        params.append(category_filter)

    if search_query:
        base_query += " AND p.name LIKE %s"
        params.append(f"%{search_query}%")

    if condition_filter:
        base_query += " AND p.condition_type = %s"
        params.append(condition_filter)

    if brand_filter:
        base_query += " AND p.brand = %s"
        params.append(brand_filter)

    if stock_status == 'available':
        base_query += " AND p.stock > 0"
    elif stock_status == 'soldout':
        base_query += " AND p.stock = 0"
    elif stock_status == 'low':
        base_query += " AND p.stock > 0 AND p.stock <= 5"

    base_query += " ORDER BY c.name, p.name"

    if params:
        cur.execute(base_query, params)
    else:
        cur.execute(base_query)

    products = cur.fetchall()

    cur.execute("SELECT * FROM categories")
    categories = cur.fetchall()

    # Get brands for filter
    cur.execute("SELECT DISTINCT brand FROM products WHERE brand IS NOT NULL")
    brands = cur.fetchall()

    cur.close()

    return render_template(
        'admin/products.html',
        products=products,
        categories=categories,
        brands=brands,
        selected_category=category_filter,
        search_query=search_query,
        condition_filter=condition_filter,
        brand_filter=brand_filter,
        stock_status=stock_status
    )


@app.route('/admin/products/edit/<int:id>', methods=['GET', 'POST'])
def edit_product(id):

    cur = mysql.connection.cursor()

    # ✅ FETCH FIRST (required for both GET and POST)
    cur.execute("SELECT * FROM products WHERE id=%s", (id,))
    product = cur.fetchone()

    if not product:
        flash("Product not found", "danger")
        return redirect(url_for('admin_products'))

    if request.method == 'POST':
        image_filename = product['image']  # ✅ now exists

        if 'image' in request.files:
            file = request.files['image']
            if file and file.filename != '' and allowed_file(file.filename):
                image_filename = secure_filename(file.filename)
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], image_filename))

        cur.execute("""
            UPDATE products SET
                name=%s,
                description=%s,
                price=%s,
                stock=%s,
                condition_type=%s,
                category_id=%s,
                image=%s
            WHERE id=%s
        """, (
            request.form['name'],
            request.form['description'],
            request.form['price'],
            request.form['stock'],
            request.form['condition'],
            request.form['category_id'],
            image_filename,
            id
        ))

        mysql.connection.commit()
        flash("Product updated successfully", "success")
        return redirect(url_for('admin_products'))

    # GET request only
    cur.execute("SELECT * FROM categories")
    categories = cur.fetchall()
    cur.close()

    return render_template(
        'admin/edit_product.html',
        product=product,
        categories=categories
    )


@app.route('/admin/products/delete/<int:id>')
def delete_product(id):
    if 'admin_id' not in session:
        flash("Unauthorized access", "danger")
        return redirect(url_for('home'))
    
    cur = mysql.connection.cursor()
    
    try:
        cur.execute("DELETE FROM products WHERE id=%s", (id,))
        mysql.connection.commit()
        flash("Product deleted", "success")
    except:
        flash("Cannot delete this product because it is linked to other records.", "warning")
    finally:
        cur.close()

    return redirect(url_for('admin_products'))

# ADMIN INVENTORY MANAGEMENT
@app.route('/admin/inventory', methods=['GET', 'POST'])
def inventory():
    cur = mysql.connection.cursor()

    # Handle stock update
    if request.method == 'POST':
        product_id = int(request.form['product_id'])
        qty = int(request.form['quantity'])
        change_type = request.form['change_type']
        remarks = request.form.get('remarks', '')

        # Get current stock
        cur.execute("SELECT stock FROM products WHERE id=%s", (product_id,))
        current_stock = cur.fetchone()['stock']

        if change_type == 'ADD':
            new_stock = current_stock + qty
        elif change_type == 'REMOVE':
            new_stock = max(0, current_stock - qty)
        else:  # ADJUST
            new_stock = qty

        # Update stock
        cur.execute(
            "UPDATE products SET stock=%s WHERE id=%s",
            (new_stock, product_id)
        )

        # Insert log
        cur.execute("""
            INSERT INTO inventory_logs
            (product_id, change_type, quantity, previous_stock, new_stock, remarks)
            VALUES (%s,%s,%s,%s,%s,%s)
        """, (product_id, change_type, qty, current_stock, new_stock, remarks))

        mysql.connection.commit()

    # Fetch data for page
    cur.execute("""
        SELECT l.*, p.name
        FROM inventory_logs l
        JOIN products p ON p.id = l.product_id
        ORDER BY l.created_at DESC
    """)
    logs = cur.fetchall()

    cur.execute("SELECT id, name FROM products ORDER BY name")
    products = cur.fetchall()

    cur.close()
    return render_template(
        'admin/inventory.html',
        logs=logs,
        products=products
    )

# ADMIN CATEGORY MANAGEMENT
@app.route('/admin/categories', methods=['GET', 'POST'])
def manage_categories():
    cur = mysql.connection.cursor()

    # ADD CATEGORY
    if request.method == 'POST':
        name = request.form['name']

        cur.execute(
            "INSERT INTO categories (name) VALUES (%s)",
            (name,)
        )
        mysql.connection.commit()

    # FETCH CATEGORIES + PRODUCT COUNT
    cur.execute("""
        SELECT c.id, c.name, COUNT(p.id) AS total_products
        FROM categories c
        LEFT JOIN products p ON p.category_id = c.id
        GROUP BY c.id
        ORDER BY c.name
    """)
    categories = cur.fetchall()

    cur.close()
    return render_template('admin/categories.html', categories=categories)

@app.route('/admin/categories/edit/<int:id>', methods=['POST'])
def edit_category(id):
    name = request.form['name']
    cur = mysql.connection.cursor()
    cur.execute(
        "UPDATE categories SET name=%s WHERE id=%s",
        (name, id)
    )
    mysql.connection.commit()
    cur.close()
    return redirect('/admin/categories')

@app.route('/admin/categories/delete/<int:id>')
def delete_category(id):
    cur = mysql.connection.cursor()

    # CHECK IF CATEGORY HAS PRODUCTS
    cur.execute(
        "SELECT COUNT(*) AS total FROM products WHERE category_id=%s",
        (id,)
    )
    count = cur.fetchone()['total']

    if count == 0:
        cur.execute(
            "DELETE FROM categories WHERE id=%s",
            (id,)
        )
        mysql.connection.commit()
    else:
        flash('Cannot delete category with existing products', 'danger')

    cur.close()
    return redirect('/admin/categories')

#ADMIN ORDER MANAGEMENT
@app.route('/admin/orders')
def admin_orders():
    if 'admin_id' not in session:
        flash("Unauthorized access", "danger")
        return redirect(url_for('home'))

    search_query = request.args.get('search', '').strip()
    status_filter = request.args.get('status', '')

    cur = mysql.connection.cursor()
    
    base_query = """
        SELECT o.*, u.fullname AS customer_name
        FROM orders o
        JOIN users u ON o.user_id = u.id
        WHERE o.status IN ('Pending','Approved','Shipped','Delivered','Declined')
    """

    params = []

    if search_query:
        base_query += " AND (u.fullname LIKE %s OR o.id LIKE %s)"
        params.append(f"%{search_query}%")
        params.append(f"%{search_query}%")

    if status_filter:
        base_query += " AND o.status = %s"
        params.append(status_filter)

    base_query += " ORDER BY o.created_at DESC"

    if params:
        cur.execute(base_query, params)
    else:
        cur.execute(base_query)
    
    orders = cur.fetchall()
    cur.close()

    return render_template('admin/orders.html', orders=orders, search_query=search_query, status_filter=status_filter)

@app.route('/admin/order/approve/<int:id>')
def approve_order(id):
    cur = mysql.connection.cursor()
    cur.execute("""
        UPDATE orders
        SET status='Approved', decline_reason=NULL
        WHERE id=%s AND status='Pending'
    """, (id,))
    mysql.connection.commit()
    cur.close()

    flash("Order approved", "success")
    return redirect(url_for('admin_orders'))

@app.route('/admin/order/decline/<int:id>', methods=['POST'])
def decline_order(id):
    reason = request.form['reason']

    cur = mysql.connection.cursor()
    cur.execute("""
        UPDATE orders
        SET status='Declined', decline_reason=%s
        WHERE id=%s AND status='Pending'
    """, (reason, id))
    mysql.connection.commit()
    cur.close()

    flash("Order declined", "danger")
    return redirect(url_for('admin_orders'))

@app.route('/admin/order/update/<int:id>', methods=['POST'])
def update_order(id):
    status = request.form.get('status')  # get from dropdown
    cur = mysql.connection.cursor()
    cur.execute(
        "UPDATE orders SET status=%s WHERE id=%s",
        (status, id)
    )
    mysql.connection.commit()
    cur.close()
    flash("Order updated!", "success")
    return redirect(url_for('admin_orders'))


from datetime import datetime, timedelta

@app.route('/admin/sales')
def admin_sales():
    if session.get('role') != 'admin':
        flash("Unauthorized access", "danger")
        return redirect(url_for('home'))

    report_type = request.args.get('type', 'daily')

    cur = mysql.connection.cursor()

    # SQL query based on report type
    if report_type == 'daily':
        query = """
            SELECT DATE(o.created_at) AS period,
                   COUNT(DISTINCT o.id) AS total_orders,
                   COALESCE(SUM(oi.quantity * oi.price), 0) AS total_sales
            FROM orders o
            JOIN order_items oi ON o.id = oi.order_id
            WHERE o.status IN ('Approved', 'Shipped', 'Delivered')
            GROUP BY DATE(o.created_at)
            ORDER BY period DESC
        """
    elif report_type == 'weekly':
        query = """
            SELECT YEARWEEK(o.created_at, 1) AS period,
                   COUNT(DISTINCT o.id) AS total_orders,
                   COALESCE(SUM(oi.quantity * oi.price), 0) AS total_sales
            FROM orders o
            JOIN order_items oi ON o.id = oi.order_id
            WHERE o.status IN ('Approved', 'Shipped', 'Delivered')
            GROUP BY YEARWEEK(o.created_at, 1)
            ORDER BY period DESC
        """
    else:  # monthly
        query = """
            SELECT DATE_FORMAT(o.created_at, '%Y-%m') AS period,
                   COUNT(DISTINCT o.id) AS total_orders,
                   COALESCE(SUM(oi.quantity * oi.price), 0) AS total_sales
            FROM orders o
            JOIN order_items oi ON o.id = oi.order_id
            WHERE o.status IN ('Approved', 'Shipped', 'Delivered')
            GROUP BY DATE_FORMAT(o.created_at, '%Y-%m')
            ORDER BY period DESC
        """

    cur.execute(query)
    reports = cur.fetchall()
    cur.close()

    # Format period for readability
    formatted_reports = []
    for r in reports:
        period = r['period']
        if report_type == 'daily':
            period = datetime.strptime(str(period), "%Y-%m-%d").strftime("%b %d, %Y")  # Jan 07, 2026
        elif report_type == 'weekly':
            # period = YEARWEEK, e.g. 202601
            year = int(str(period)[:4])
            week = int(str(period)[4:])
            monday = datetime.strptime(f'{year}-W{week}-1', "%Y-W%W-%w")
            sunday = monday + timedelta(days=6)
            period = f"{monday.strftime('%b %d')} – {sunday.strftime('%b %d, %Y')}"
        else:  # monthly
            period = datetime.strptime(str(period), "%Y-%m").strftime("%B %Y")  # January 2026

        formatted_reports.append({
            'period': period,
            'total_orders': r['total_orders'],
            'total_sales': r['total_sales']
        })

    return render_template(
        'admin/sales.html',
        reports=formatted_reports,
        report_type=report_type
    )

@app.route('/admin/sales/export')
def export_sales():
    if session.get('role') != 'admin':
        flash("Unauthorized access", "danger")
        return redirect(url_for('home'))

    report_type = request.args.get('type', 'daily')
    format = request.args.get('format', 'pdf')

    if report_type == 'weekly':
        group = "YEARWEEK(o.created_at, 1)"  # Week starts on Monday
    elif report_type == 'monthly':
        group = "DATE_FORMAT(o.created_at, '%Y-%m')"
    else:
        group = "DATE(o.created_at)"

    cur = mysql.connection.cursor()
    cur.execute(f"""
        SELECT 
            {group} AS period,
            COUNT(DISTINCT o.id) AS total_orders,
            SUM(oi.quantity * oi.price) AS total_sales
        FROM orders o
        JOIN order_items oi ON o.id = oi.order_id
        WHERE LOWER(o.status) IN ('approved','shipped','delivered')
        GROUP BY {group}
        ORDER BY {group} DESC
    """)
    rows = cur.fetchall()
    cur.close()

    # Format period like in the web table
    formatted_data = []
    for r in rows:
        period = r['period']
        total_sales = r['total_sales'] if r['total_sales'] is not None else 0  # Handle NULL
        if report_type == 'daily':
            period = datetime.strptime(str(period), "%Y-%m-%d").strftime("%b %d, %Y")  # Jan 07, 2026
        elif report_type == 'weekly':
            # period = YEARWEEK, e.g. 202601
            year = int(str(period)[:4])
            week = int(str(period)[4:])
            monday = datetime.strptime(f'{year}-W{week}-1', "%Y-W%W-%w")
            sunday = monday + timedelta(days=6)
            period = f"{monday.strftime('%b %d')} – {sunday.strftime('%b %d, %Y')}"
        else:  # monthly
            period = datetime.strptime(str(period), "%Y-%m").strftime("%B %Y")  # January 2026

        formatted_data.append({
            'period': period,
            'total_orders': r['total_orders'],
            'total_sales': total_sales
        })

    if format == 'pdf':
        return export_sales_pdf(formatted_data)
    else:
        return export_sales_docx(formatted_data)
    
@app.route('/admin/users')
def admin_users():
    # make sure only admins can access
    if session.get('role') != 'admin':
        return redirect('/login')

    search_query = request.args.get('search', '').strip()
    role_filter = request.args.get('role', '')
    status_filter = request.args.get('status', '')

    cur = mysql.connection.cursor()
    
    base_query = "SELECT id, fullname, email, role, is_active FROM users WHERE 1=1"
    params = []

    if search_query:
        base_query += " AND (fullname LIKE %s OR email LIKE %s)"
        params.append(f"%{search_query}%")
        params.append(f"%{search_query}%")

    if role_filter:
        base_query += " AND role = %s"
        params.append(role_filter)

    if status_filter:
        if status_filter == 'active':
            base_query += " AND is_active = 1"
        elif status_filter == 'inactive':
            base_query += " AND is_active = 0"

    base_query += " ORDER BY id DESC"

    if params:
        cur.execute(base_query, params)
    else:
        cur.execute(base_query)
    
    users = cur.fetchall()
    cur.close()
    
    return render_template('admin/users.html', users=users, search_query=search_query, role_filter=role_filter, status_filter=status_filter)

@app.route('/admin/users/reset-password/<int:user_id>', methods=['POST'])
def reset_user_password(user_id):
    if session.get('role') != 'admin':
        return redirect('/login')

    new_password = request.form['password']
    hashed = generate_password_hash(new_password)

    cur = mysql.connection.cursor()
    cur.execute(
        "UPDATE users SET password=%s WHERE id=%s",
        (hashed, user_id)
    )
    cur.connection.commit()

    flash("Password reset successfully", "success")
    return redirect(url_for('admin_users'))

@app.route('/admin/users/toggle/<int:user_id>')
def toggle_user(user_id):
    if session.get('role') != 'admin':
        return redirect('/login')

    cur = mysql.connection.cursor()
    cur.execute(
        "UPDATE users SET is_active = NOT is_active WHERE id=%s",
        (user_id,)
    )
    cur.connection.commit()

    flash("User status updated", "success")
    return redirect(url_for('admin_users'))


@app.route('/admin/logout')
def admin_logout():
    session.clear()
    return redirect(url_for('admin_login'))


if __name__ == "__main__":
    
    app.run(debug=True)